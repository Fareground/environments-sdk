"""Actions: which are legal, argument validation, atomic apply.

Argument validation lives in :mod:`.validation`, and the parameter limits it shares with the tool schemas
(:mod:`fg_env.information.schemas`) in :mod:`.params`.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from ..assets.delivery import attached_ids
from ..contract import ActionSpec, Contract, ParamSpec, StageSpec
from ..effects.runner import EffectRunner
from ..errors import RunError
from ..expr import (
    EVAL_BUDGET,
    EVERYONE,
    Expr,
    ExprError,
    PrivateRead,
    Scope,
    compile_expr,
    is_expr,
    shared_budget,
    truthy,
)
from ..expr.objects import Entity
from ..expr.template import format_value
from ..information.announce import Redaction, notified_since
from ..information.gate import render
from ..information.schemas import _ENUM_CHOICES
from ..world.abort import Abort
from ..world.randomness import LuckAhead
from ..world.store import World
from ..world.values import plain_value
from .faults import fault_reason
from .params import MAX_SAFE_INT, TEXT_MAX_LEN, item_spec, tidy
from .validation import ActionValidation

__all__ = ["ACTION_BUDGET", "TEXT_MAX_LEN", "MAX_SAFE_INT", "Outcome", "ActionBook", "stage_actions",
           "announces"]

#: Work one action application may do in total (all its conditions, effects and templates).
ACTION_BUDGET = 5 * EVAL_BUDGET
#: Why nothing that decides whether a call is allowed, or what its arguments may be, may draw at random.
UNDECIDED_BY_LUCK = ("luck cannot decide whether a call is allowed or what its arguments may be: a refused call costs "
                     "nothing, so an agent could call again until luck let it through. Draw in the action's `do` (or "
                     "use its `chance`), or in an event that stores the result for this to read")


@dataclass
class Outcome:
    ok: bool
    text: str
    params: dict[str, Any] = field(default_factory=dict)
    #: The assets the action's `attach` delivers to its actor.
    assets: list[str] = field(default_factory=list)


def stage_actions(contract: Contract, stage: StageSpec, type_name: str) -> list[str]:
    """Action names available to ``type_name`` during ``stage`` (before per-turn legality)."""
    spec = stage.actions
    if spec == "all":
        names = list(contract.actions)
    elif isinstance(spec, list):
        names = list(spec)
    elif isinstance(spec, dict):
        names = []
        for kind in reversed(contract.lineage(type_name)):  # the most specific type's list wins
            if kind in spec:
                names = list(spec[kind])
                break
    else:
        names = []
    return [name for name in names if contract.can_take(type_name, name)]


def announces(contract: Contract, stage: StageSpec) -> bool:
    """Whether some agent's action in ``stage`` is announced to everyone (not `announce: false`): then everyone learns
    who acts in it."""
    return any(not contract.actions[name].silent
               for kind in contract.agent_types() for name in stage_actions(contract, stage, kind))


class ActionBook:
    def __init__(self, contract: Contract, world: World, effects: EffectRunner):
        self.contract = contract
        self.world = world
        self.effects = effects
        #: What each action's announcement may repeat of its arguments.
        self.redaction = Redaction(contract)
        #: Resolves what a participant passed to an action into typed values, or a correction.
        self.validation = ActionValidation(self)

    # -- legality -------------------------------------------------------------

    def blocked(self, actor: Entity, name: str, used_turn: dict[str, int], used_round: dict[str, int],
                offered: bool = False) -> str | None:
        """Why ``name`` is not legal for ``actor`` right now, or None when it is. ``offered``: whether to offer it as
        a tool, where a requirement that reads something hidden from the actor does not count — the tool is listed
        and a call is refused if the requirement fails, so the list itself reveals nothing hidden. A turn asks this
        several times in the same state (its tools, a coded policy's rule, the call itself), so the answer is
        remembered."""
        key = ("blocked", actor.id, name, used_turn.get(name, 0), used_round.get(name, 0), offered)
        # Offering is no attempt: what it reads hidden counts toward no call around it.
        with self.deciding(), self.world.luck.apart() if offered else nullcontext():
            return self.world.evaluation.remembered(
                key, lambda: self._blocked(actor, name, used_turn, used_round, offered))

    def validate(self, actor: Entity, name: str, args: Any) -> tuple[dict[str, Any], str | None]:
        """``args`` for ``name`` as typed values: ``(params, None)``, or ``({}, correction)`` (see
        :meth:`ActionValidation.validate`)."""
        return self.validation.validate(actor, name, args)

    def deciding(self) -> Any:
        """A block that decides whether a call is allowed or what its arguments may be: a random draw in it fails as a
        rule (see :data:`UNDECIDED_BY_LUCK`)."""
        return self.world.luck.forbidden(UNDECIDED_BY_LUCK)

    def _blocked(self, actor: Entity, name: str, used_turn: dict[str, int], used_round: dict[str, int],
                 offered: bool) -> str | None:
        spec = self.contract.actions[name]
        if not actor.alive:
            return "you are no longer active"
        if spec.per_turn is not None and used_turn.get(name, 0) >= spec.per_turn:
            return f"{name} can be used {spec.per_turn} time(s) per turn"
        if spec.per_round is not None and used_round.get(name, 0) >= spec.per_round:
            return f"{name} can be used {spec.per_round} time(s) per round"
        refused = self.unmet(actor, name, None, offered)
        if refused is not None:
            return refused
        for pname, param in spec.params.items():
            if not self.required(param) or self.depends_on_params(param):
                continue
            if param.type == "entity" and not self.choices(actor, name, pname, param, first=True):
                return f"there is no {param.of or 'target'} you can choose for {pname} right now"
            if param.type in ("number", "int"):
                empty = self._empty_range(actor, param, f"actions.{name}.params.{pname}")
                if empty is not None:
                    return f"there is no valid {pname} right now ({empty})"
            if param.type == "enum" and isinstance(param.values, str) \
                    and self.static(actor, param.values, f"actions.{name}.params.{pname}.values") == []:
                return f"there is no value you can choose for {pname} right now"
            if param.type == "list" and isinstance(param.min_items, int) and param.min_items > 0:
                item = item_spec(param)
                if item.type == "entity" and item.of is not None and not self.depends_on_params(item) \
                        and len(self.choices(actor, name, pname, item)) < param.min_items:
                    return f"there are not enough {item.of} entities to choose for {pname} right now"
                values = (self.static(actor, param.values, f"actions.{name}.params.{pname}.values")
                          if isinstance(param.values, str) else param.values)
                if isinstance(values, list) and len(values) < param.min_items:
                    return f"there are not enough values to choose for {pname} right now"
        return None

    def unmet(self, actor: Entity, name: str, params: dict[str, Any] | None, offered: bool = False) -> str | None:
        """The `why` of the first requirement that does not hold: those over $actor alone (``params`` None), or
        those that read $params. ``offered``: leave out those that read something hidden from the actor — the tool is
        listed and a call refused if the requirement fails, so the list itself reveals nothing hidden."""
        vars: dict[str, Any] = {"actor": actor} if params is None else {"actor": actor, "params": params}
        scope: Scope | None = None
        luck = self.world.luck
        for index, condition in enumerate(self.contract.actions[name].when):
            compiled = compile_expr(condition.expr)
            if ("params" in compiled.roots) is not (params is not None):
                continue
            if scope is None:  # built for the first requirement evaluated
                scope = self.world.evaluation.scope(**vars)
            path = f"actions.{name}.when[{index}]"
            observed = luck.observe()
            try:
                if truthy(compiled(scope)):
                    continue
            except ExprError as exc:
                raise RunError(str(exc), path) from None
            if offered and observed.read_hidden:
                continue
            # the why is a template, like a `fail` text: text the actor is shown
            why = render(self.world, condition.why, vars, viewer=actor, path=f"{path}.why") if condition.why else ""
            return (why or "its requirements are not met").rstrip(". ")
        return None

    def _empty_range(self, actor: Entity, param: ParamSpec, where: str) -> str | None:
        """The bounds, when no value lies between them right now (min above max)."""
        low, high = (tidy(self.static(actor, param.min, f"{where}.min")),
                     tidy(self.static(actor, param.max, f"{where}.max")))
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (low, high)):
            return None
        least, most = (math.ceil(low), math.floor(high)) if param.type == "int" else (low, high)
        return f"at least {format_value(low)} and at most {format_value(high)}" if least > most else None

    def static(self, actor: Entity, raw: Any, where: str) -> Any:
        """Evaluate a bound that depends only on the actor; None when it needs call arguments. One that reads another
        agent's private property is an error at ``where``: the tool would be offered without it, and refused."""
        if not is_expr(raw):
            return raw
        expr = compile_expr(raw)
        if "params" in expr.roots:
            return None
        try:
            return expr(self.world.evaluation.scope(actor=actor, viewer=actor))
        except PrivateRead as exc:
            raise RunError(str(exc), where) from None
        except ExprError:
            return None

    @staticmethod
    def required(param: ParamSpec) -> bool:
        return param.required if param.required is not None else param.default is None

    @staticmethod
    def depends_on_params(param: ParamSpec) -> bool:
        return param.where is not None and "params" in compile_expr(param.where).roots

    @staticmethod
    def values_depend_on_params(param: ParamSpec) -> bool:
        return isinstance(param.values, str) and "params" in compile_expr(param.values).roots

    def choices(self, actor: Entity, action: str, pname: str, param: ParamSpec,
                 params: dict[str, Any] | None = None, first: bool = False) -> list[Entity]:
        """Entities that qualify. A `where` over earlier params is applied once they are known
        (at validation); before that (tool schemas) every entity of the type is listed. With
        ``first``, stop at the first one (enough to know whether any qualifies). The list may be shared: read it,
        never change it."""
        if param.of is None:
            raise RunError("an entity parameter needs `of` (the entity type)", f"actions.{action}.params.{pname}")
        items = self.world.alive_of(param.of)
        if param.where is None:
            return items
        expr = compile_expr(param.where)
        if "params" in expr.roots:
            return items if params is None else self._qualifying(actor, action, pname, expr, items, params, first)
        if first:
            return self._qualifying(actor, action, pname, expr, items, None, first)
        return self.world.evaluation.remembered(
            ("choices", action, pname, actor.id, param.of, param.where),
            lambda: self._qualifying(actor, action, pname, expr, items, None, False))

    def _qualifying(self, actor: Entity, action: str, pname: str, expr: Any, items: list[Entity],
                    params: dict[str, Any] | None, first: bool) -> list[Entity]:
        """The ``items`` the `where` ``expr`` picks, read as the actor sees them."""
        out = []
        base = self.world.evaluation.scope(actor=actor, viewer=actor, params=params or {})
        ruled_out, ruled_in = expr.rules_out(base), expr.rules_in(base)
        for position, item in enumerate(items):
            if ruled_out is not None and ruled_out(item):
                continue
            if ruled_in is not None and ruled_in(item):
                out.append(item)
                if first:
                    break
                continue
            try:
                if truthy(expr(base.child(it=item, i=position))):
                    out.append(item)
                    if first:
                        break
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{action}.params.{pname}.where") from None
        return out

    def fill_dependent(self, actor: Entity, name: str, args: dict[str, Any],
                       pick: Callable[[list[Any]], Any]) -> dict[str, Any]:
        """``args`` with each argument its tool cannot list the exact choices of — an entity or enum whose choices
        depend on earlier arguments, or entities too many to enumerate — set to ``pick`` of the choices that qualify,
        for participants that choose arguments from the tool schema without reading the rules. It stops at the first
        earlier argument that is missing or invalid, or a choice with nothing to pick: validation then says what to
        fix."""
        with self.deciding():
            return self._fill_dependent(actor, name, args, pick)

    def _fill_dependent(self, actor: Entity, name: str, args: dict[str, Any],
                        pick: Callable[[list[Any]], Any]) -> dict[str, Any]:
        spec = self.contract.actions[name]
        unlisted = {pname for pname, p in spec.params.items() if (p.type == "entity" and (
            self.depends_on_params(p) or len(self.choices(actor, name, pname, p)) > _ENUM_CHOICES))
            or (p.type == "enum" and self.values_depend_on_params(p))}
        if not unlisted:
            return args
        filled: dict[str, Any] = dict(args)
        params: dict[str, Any] = {}
        for pname, param in spec.params.items():
            if pname in unlisted:
                entity = param.type == "entity"
                options = self.choices(actor, name, pname, param, params) if entity else \
                    self.validation.enum_values(actor, name, pname, param, params)
                if not options:
                    filled.pop(pname, None)
                    break
                chosen = pick(options)
                params[pname], filled[pname] = chosen, chosen.id if entity else chosen
                continue
            raw = filled.get(pname)
            if raw is None:
                break
            value, problem = self.validation.value(actor, name, pname, param, raw, params)
            if problem:
                break
            params[pname] = value
        return filled

    # -- apply ---------------------------------------------------------------------

    def apply(self, actor: Entity, name: str, params: dict[str, Any]) -> Outcome:
        """Apply atomically. A `fail` effect or failed transfer rolls back and returns ok=False.
        Everything the action evaluates shares one work budget, so a loop of effects is bounded
        as a whole, not only each expression in it."""
        with shared_budget(ACTION_BUDGET, f"actions.{name}"):
            return self._apply(actor, name, params)

    def _apply(self, actor: Entity, name: str, params: dict[str, Any], trial: bool = False) -> Outcome:
        """Apply atomically. A ``trial`` (a dry run, rolled back by the caller) leaves out the default outcome text,
        the announcement and its event: they cannot fail or draw, and a rollback would undo them unseen. The action
        draws from its actor's own stream, so it never shifts another agent's luck or the world's; a refusal keeps
        what it drew spent, so retrying rolls fresh luck (see :class:`~fg_env.sampling.seeds.DrawSite`)."""
        with self.world.luck.at(f"actions.{name}", actor), self.world.luck.acting_as(actor, name):
            return self._apply_drawn(actor, name, params, trial)

    def _apply_drawn(self, actor: Entity, name: str, params: dict[str, Any], trial: bool) -> Outcome:
        """:meth:`_apply` inside the action's draw site."""
        spec: ActionSpec = self.contract.actions[name]
        world = self.world
        mark = world.mark()
        vars: dict[str, Any] = {"actor": actor, "params": params}
        path = f"actions.{name}"
        log_mark = world.log[-1].seq if world.log else 0
        record_mark = world.record_seq
        try:
            self.effects.run(spec.do, vars, f"{path}.do")
            text = render(world, spec.outcome, vars, viewer=actor, path=f"{path}.outcome") if spec.outcome else \
                "" if trial else self.default_outcome(name, params)
            assets = attached_ids(world, spec.attach, world.evaluation.scope(**vars), f"{path}.attach") \
                if spec.attach else []
            announce = spec.announce
            shared = {**vars, "params": self.redaction.shared(name, params)}  # what text sent to several reads
            if trial:
                if isinstance(announce, str):
                    render(world, announce, shared, viewer=EVERYONE, path=f"{path}.announce")
            elif announce is not False:
                public = self.redaction.public_params(world, name, params, record_mark)
                if announce is not None:
                    line = render(world, announce, shared, viewer=EVERYONE, path=f"{path}.announce")
                elif notified_since(world, log_mark):
                    line = ""  # the posted entry itself is the news
                else:
                    line = self._default_announce(actor, name, public)
                # Public, unless it posted entries not every agent may see and says nothing of its own; the actor's
                # own announcement is filtered out of its news by perception.
                data = {"action": name, "params": plain_value(public), "success": True}
                restricted = self.redaction.restricted_since(world, record_mark) if announce is None else None
                if restricted:
                    data["posted"] = restricted  # seen only by the readers who may see these entries
                announcement = world.emit("action", line, actor=actor.id, to=None, data=data)
                world.put_first(announcement, log_mark)
            else:
                world.emit("action", "", actor=actor.id, to=(actor.id,),
                           data={"action": name, "params": plain_value(params), "success": True, "private": True})
        except Abort as abort:
            world.rollback(mark)
            return Outcome(False, abort.reason, params)
        except ExprError as exc:
            world.rollback(mark)
            raise RunError(str(exc), path) from None
        except RunError:
            world.rollback(mark)
            raise
        return Outcome(True, text, params, assets)

    def ends_turn(self, actor: Entity, name: str, params: dict[str, Any]) -> bool:
        """Whether the call ends the actor's turn (`terminal`): something its actor is shown, so read as it sees."""
        terminal = self.contract.actions[name].terminal
        if isinstance(terminal, bool):
            return terminal
        try:
            return truthy(compile_expr(terminal)(self.world.evaluation.scope(actor=actor, viewer=actor, params=params)))
        except ExprError as exc:
            raise RunError(str(exc), f"actions.{name}.terminal") from None

    @contextmanager
    def trying(self) -> Iterator[None]:
        """Nothing done inside the block stays: its changes are undone on the way out."""
        world = self.world
        mark = world.mark()
        try:
            yield
        finally:
            world.rollback(mark)

    def trial(self, actor: Entity, name: str, params: dict[str, Any]) -> str | None:
        """Apply inside :meth:`trying`, to catch a doomed call before it is made: the refusal, or None. A trial draws
        nothing and asks no chance picker: at its first random draw it stops and refuses nothing, because what follows
        is luck, and telling it would let an agent probe its luck before playing (the call itself rolls it)."""
        world = self.world
        picker, world.chance_picker = world.chance_picker, None
        try:
            with shared_budget(ACTION_BUDGET, f"actions.{name}"), world.luck.forbidden():
                outcome = self._apply(actor, name, params, trial=True)
        except LuckAhead:
            return None
        finally:
            world.chance_picker = picker
        return None if outcome.ok else outcome.text

    def dry_run(self, actor: Entity, name: str, params: dict[str, Any]) -> str | None:
        """:meth:`trial` and roll back: the refusal, or None."""
        with self.trying():
            return self.trial(actor, name, params)

    def replay(self, actor: Entity, intents: Sequence[tuple[str, dict[str, Any]]]) -> None:
        """Apply ``actor``'s sealed choices inside :meth:`trying`, as their commit will, so its next choice is tried
        against the state they leave (two buys cannot spend the same coins)."""
        for name, args in intents:
            params, problem = self.validate(actor, name, args)
            if not problem:
                self.trial(actor, name, params)

    def refusal(self, actor: Entity, name: str, params: dict[str, Any]) -> str | None:
        """:meth:`dry_run` for code that only asks whether a call would work (tool probes, legal-call listings): a rule
        that fails for the call refuses it, as it would if an agent made it."""
        try:
            return self.dry_run(actor, name, params)
        except RunError as exc:
            return fault_reason(exc)

    def known_refusal(self, actor: Entity, name: str, args: dict[str, Any], dry_run: bool = True) -> str | None:
        """Why ``actor``'s call of ``name`` with ``args`` would be refused — validated, then (``dry_run``) tried —
        when knowing it costs nothing: what decided it drew no luck and read nothing hidden from the actor. A refusal
        that turned on either is None, as the live call would be: legal, and spent if refused when applied (see
        runtime/ledger.py). Legal-call listings ask this, so they reveal no more than a call would."""
        observed = self.world.luck.observe()
        params, problem = self.validate(actor, name, args)
        if problem is None and dry_run:
            problem = self.refusal(actor, name, params)
        return None if observed.spends else problem

    @staticmethod
    def _args_text(params: dict[str, Any]) -> str:
        parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
        return f" ({', '.join(parts)})" if parts else ""

    def default_outcome(self, name: str, params: dict[str, Any]) -> str:
        return f"Done: {name.replace('_', ' ')}{self._args_text(params)}."

    def _default_announce(self, actor: Entity, name: str, params: dict[str, Any]) -> str:
        return f"{actor.name}: {name.replace('_', ' ')}{self._args_text(params)}."
