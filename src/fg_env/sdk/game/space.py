"""Actions for game code: stable integer ids for tool calls, and the legal calls of a turn.

The action space is fixed when a game is created. Every action (``end_turn`` first, then the
contract's actions in order) gets a block of ids: one per combination of its parameters' values —
enum values, entity ids, booleans, whole numbers (or numbers with a `step`) between their bounds,
and "left out" for optional parameters. Domains computed from state (``"values": "$actor.hand"``,
``"max": "$world.stones"``) use every value they give at the start of the game. An action whose
arguments cannot be listed — free text, lists, numbers without a step, too many combinations — is
parametric: it has one id, and a call to it carries its own arguments.

Legal calls of a turn are found the way the engine judges an agent's call: the offered actions,
every combination of the parameters' current choices, validated, and tried without effect.
"""
from __future__ import annotations

import bisect
import math
import random
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from ...entity import Entity
from ..actions import TrialStream
from ..contract import ParamSpec
from ..errors import RunError
from ..expr import ExprError, compile_expr, is_expr
from ..session import END_TURN
from ..template import format_value
from ..world import _plain

if TYPE_CHECKING:
    from ..runtime import Env
    from ..turn import Turn

__all__ = ["Action", "ActionSpace", "legal_calls", "sample_call", "COMBINATION_LIMIT"]

#: Most parameter combinations listed for one action (in the action space, and legal in one turn).
COMBINATION_LIMIT = 10_000
_NUMBER_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Action:
    """One tool call. ``id`` is None for a call to a parametric action (its arguments have no id)."""

    id: Optional[int]
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        shown = ", ".join(f"{key}={format_value(value)}" for key, value in self.args.items())
        return f"{self.tool}({shown})" if shown else self.tool

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class _Block:
    tool: str
    offset: int
    size: int
    params: Tuple[Tuple[str, Tuple[Any, ...]], ...] = ()
    parametric: Optional[str] = None


class ActionSpace:
    """Every tool call a game can make, numbered: ``encode`` a call, ``decode`` an id."""

    def __init__(self, env: "Env", limit: int = COMBINATION_LIMIT):
        contract, world = env.contract, env.world
        self.limit = limit
        blocks: List[_Block] = [_Block(END_TURN, 0, 1)]
        offset = 1
        for name, spec in contract.actions.items():
            by = [spec.by] if isinstance(spec.by, str) else spec.by
            actors = [e for e in world.entities.values() if e.alive and any(contract.is_a(e.entity_type, b) for b in by)]
            params: List[Tuple[str, Tuple[Any, ...]]] = []
            reason: Optional[str] = None
            size = 1
            for pname, param in spec.params.items():
                universe, why = _universe(env, param, actors, limit)
                if universe is None:
                    reason = f"{pname}: {why}"
                    break
                if not env.actions._required(param):
                    universe = [None] + universe
                size *= max(1, len(universe))
                if size > limit:
                    reason = f"more than {limit:,} combinations of arguments"
                    break
                params.append((pname, tuple(universe)))
            block = _Block(name, offset, 1, (), reason) if reason else _Block(name, offset, size, tuple(params))
            blocks.append(block)
            offset += block.size
        self._blocks = blocks
        self._by_tool = {block.tool: block for block in blocks}
        self._offsets = [block.offset for block in blocks]
        self.size = offset

    @property
    def parametric(self) -> Dict[str, str]:
        """Actions whose calls carry their own arguments, with the reason."""
        return {block.tool: block.parametric for block in self._blocks if block.parametric}

    def encode(self, tool: str, args: Mapping[str, Any]) -> Optional[int]:
        """The id of a call, or None when it has none (a parametric action, or a value outside the space)."""
        block = self._by_tool.get(tool)
        if block is None:
            return None
        if block.parametric:
            return None
        names = {name for name, _ in block.params}
        if any(key not in names for key in args):
            return None
        index = 0
        for name, universe in block.params:
            position = _position(universe, _plain(args.get(name)))
            if position is None:
                return None
            index = index * len(universe) + position
        return block.offset + index

    def decode(self, action_id: int) -> Tuple[str, Dict[str, Any]]:
        """The call an id stands for. A parametric action's id raises: its call needs its arguments."""
        if isinstance(action_id, bool) or not isinstance(action_id, int) or not 0 <= action_id < self.size:
            raise ValueError(f"action id must be a whole number from 0 to {self.size - 1}, got {action_id!r}")
        block = self._blocks[bisect.bisect_right(self._offsets, action_id) - 1]
        if block.parametric:
            raise ValueError(f"id {action_id} is the parametric action '{block.tool}' ({block.parametric}); "
                             "apply it as a call with its arguments: {\"tool\": ..., \"args\": {...}}")
        index = action_id - block.offset
        args: Dict[str, Any] = {}
        for name, universe in reversed(block.params):
            index, position = divmod(index, len(universe))
            if universe[position] is not None:
                args[name] = universe[position]
        return block.tool, dict(reversed(list(args.items())))

    def action(self, tool: str, args: Mapping[str, Any]) -> Action:
        return Action(self.encode(tool, args), tool, MappingProxyType(dict(args)))


def _universe(env: "Env", param: ParamSpec, actors: Sequence[Entity], limit: int) -> Tuple[Optional[List[Any]], str]:
    world = env.world
    kind = param.type
    if kind == "bool":
        return [False, True], ""
    if kind == "entity":
        return ([entity.id for entity in world.entities_of(param.of)], "") if param.of in env.contract.types else \
            (None, "it names no entity type")
    if kind == "enum":
        if not isinstance(param.values, str):
            return _unique(_plain(value) for value in param.values or []), ""
        return _per_actor(world, param.values, actors, lambda value, out: out.extend(_plain(v) for v in value or []))
    if kind in ("int", "number"):
        step = param.step if param.step is not None else (1 if kind == "int" else None)
        if step is None:
            return None, "a number without a `step` has no finite set of values"
        lows, why = _per_actor(world, param.min, actors, lambda value, out: out.append(value))
        highs, why_high = _per_actor(world, param.max, actors, lambda value, out: out.append(value))
        if lows is None or highs is None:
            return None, why or why_high
        numbers = [v for v in lows + highs if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(numbers) != len(lows) + len(highs) or not lows or not highs:
            return None, "it needs `min` and `max` to list its values"
        return _steps(min(lows), max(highs), step, kind, limit)
    return None, "free text" if kind == "text" else "a list argument"


def _per_actor(world: Any, raw: Any, actors: Sequence[Entity], add: Any) -> Tuple[Optional[List[Any]], str]:
    if raw is None:
        return [], ""
    if not is_expr(raw):
        out: List[Any] = []
        add(raw, out)
        return out, ""
    expr = compile_expr(raw)
    if "params" in expr.roots:
        return None, "its choices depend on other arguments"
    out = []
    for actor in actors:
        try:
            add(expr(world.scope(actor=actor, viewer=actor)), out)
        except ExprError:
            return None, "its choices cannot be worked out at the start of the game"
    return _unique(out), ""


def _steps(low: float, high: float, step: float, kind: str, limit: int) -> Tuple[Optional[List[Any]], str]:
    count = math.floor((high - low) / step + _NUMBER_TOLERANCE) + 1
    if count > limit:
        return None, f"{count:,} values (more than {limit:,})"
    values = [low + k * step for k in range(max(0, count))]
    return [int(round(v)) if kind == "int" else float(f"{v:.12g}") for v in values], ""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unique(values: Any) -> List[Any]:
    out: List[Any] = []
    for value in values:
        if _position(out, value) is None:
            out.append(value)
    return out


def _position(universe: Sequence[Any], value: Any) -> Optional[int]:
    for index, item in enumerate(universe):
        if isinstance(item, bool) != isinstance(value, bool):
            continue
        if item == value:
            return index
        if isinstance(item, float) and isinstance(value, (int, float)) and not isinstance(value, bool) and \
                math.isclose(item, value, abs_tol=_NUMBER_TOLERANCE):
            return index
    return None


# -- the legal calls of a turn ----------------------------------------------------------------------------


class _Unlisted(Exception):
    pass


def sample_call(env: "Env", turn: "Turn", rng: random.Random, *, limit: int = COMBINATION_LIMIT,
                dry_run: bool = True) -> Optional[Tuple[str, Dict[str, Any]]]:
    """A uniformly random legal listed call of ``turn`` (None when there is none), found by trying the calls that
    validate in random order and dry-running only until one is not refused — the same choice as picking at
    random from :func:`legal_calls`, for a fraction of the work. Runs on the run's thread."""
    candidates, _ = legal_calls(env, turn, limit=limit, dry_run=False)
    order = list(range(len(candidates)))
    rng.shuffle(order)
    if not dry_run:
        return candidates[order[0]] if order else None
    book, actor = env.actions, turn.actor
    with env._lock, as_turn(env, turn):
        stream = TrialStream(env.world)
        for index in order:
            tool, args = candidates[index]
            if tool == END_TURN:
                return tool, args
            params, problem = book.validate(actor, tool, args)
            if problem is None and book.dry_run(actor, tool, params, stream) is None:
                return tool, args
    return None


def legal_calls(env: "Env", turn: "Turn", *, limit: int = COMBINATION_LIMIT,
                dry_run: bool = True) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, str]]:
    """``(calls, unlisted)``: every legal tool call of ``turn`` whose arguments can be listed, and the
    legal actions whose calls cannot, with the reason. Runs on the run's thread."""
    if turn.done:
        return [], {}
    calls: List[Tuple[str, Dict[str, Any]]] = []
    unlisted: Dict[str, str] = {}
    with env._lock, as_turn(env, turn):
        names = turn._legal()
        acted = turn.actions_left < turn.stage.max_actions or bool(turn.intents)
        if not (turn.stage.must_act and not acted and names):
            calls.append((END_TURN, {}))
        stream = TrialStream(env.world) if dry_run else None
        for name in names:
            found: List[Tuple[str, Dict[str, Any]]] = []
            try:
                _walk(env, turn, name, list(env.contract.actions[name].params.items()), 0, {}, {}, found, limit, stream)
            except _Unlisted as reason:
                unlisted[name] = str(reason)
                continue
            calls.extend(found)
    return calls, unlisted


def _walk(env: "Env", turn: "Turn", name: str, items: List[Tuple[str, ParamSpec]], index: int, raw: Dict[str, Any],
          resolved: Dict[str, Any], found: List[Tuple[str, Dict[str, Any]]], limit: int,
          stream: Optional[TrialStream]) -> None:
    """Every call of ``name`` from here on; ``stream`` is the saved random stream when calls are dry-run, else None."""
    book, actor = env.actions, turn.actor
    if index == len(items):
        params, problem = book.validate(actor, name, raw)
        if problem is None and (stream is None or book.dry_run(actor, name, params, stream) is None):
            if len(found) >= limit:
                raise _Unlisted(f"more than {limit:,} legal combinations of arguments")
            found.append((name, dict(raw)))
        return
    pname, param = items[index]
    for value in _choices(env, turn, name, pname, param, resolved, limit):
        if value is None:
            _walk(env, turn, name, items, index + 1, raw, resolved, found, limit, stream)
            continue
        typed, problem = book._value(actor, name, pname, param, value, resolved)
        if problem is None:
            _walk(env, turn, name, items, index + 1, {**raw, pname: value}, {**resolved, pname: typed}, found, limit,
                  stream)


def _choices(env: "Env", turn: "Turn", name: str, pname: str, param: ParamSpec, resolved: Dict[str, Any],
             limit: int) -> List[Any]:
    book, world, actor = env.actions, env.world, turn.actor
    head: List[Any] = [] if book._required(param) else [None]
    path = f"actions.{name}.params.{pname}"

    def scope() -> Any:  # built only for a domain that is an expression
        return world.scope(actor=actor, viewer=actor, params=resolved)

    try:
        if param.type == "bool":
            return head + [False, True]
        if param.type == "entity":
            return head + [choice.id for choice in book._choices(actor, name, pname, param, resolved)]
        if param.type == "enum":
            values = compile_expr(param.values)(scope()) if isinstance(param.values, str) else param.values
            return head + [_plain(value) for value in values or []]
        if param.type in ("int", "number"):
            step = param.step if param.step is not None else (1 if param.type == "int" else None)
            low = compile_expr(param.min)(scope()) if is_expr(param.min) else param.min
            high = compile_expr(param.max)(scope()) if is_expr(param.max) else param.max
            if step is not None and _is_number(low) and _is_number(high):
                values, why = _steps(float(low), float(high), step, param.type, limit)  # type: ignore[arg-type]
                if values is not None:
                    return head + values
                raise _Unlisted(f"{pname}: {why}")
            reason = "a number without a `step`" if step is None else "it needs `min` and `max` to list its values"
            raise _Unlisted(f"{pname}: {reason}")
    except ExprError as exc:
        raise RunError(str(exc), path) from None
    if head:
        return head  # optional free text or list: listed left out
    raise _Unlisted(f"{pname}: {'free text' if param.type == 'text' else 'a list argument'}")


@contextmanager
def as_turn(env: "Env", turn: "Turn") -> Iterator[None]:
    """Read the world as ``turn`` would (its sealed choices as $pending) with a throwaway random stream,
    so looking never draws from the run's streams; the context's own settings come back afterwards."""
    world = env.world
    with world.turn_context(env.seeds.rng("look", world.round, turn.number), turn.pending):
        yield
