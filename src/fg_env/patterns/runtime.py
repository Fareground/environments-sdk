"""Patterns in a running world: ``$pattern.<name>`` reads, calls with arguments and a key, and memory commits.

Parameters are fixed for a run: they read ``$inputs``, the key (``$key``), the key's table row (``$row``) and draw
patterns, and are evaluated once per run and key. So a pattern is a function of time, key and chance — a snapshot
holds nothing about it but the state of memory patterns (a world property), and a restored, cloned or forked run
reads the same values. Random kinds draw from streams derived from the run seed, the pattern's name and the key,
never from the shared stream: adding a pattern never shifts another draw, and every arm of an experiment sees the
same random paths (common random numbers). A fork that changes inputs reads every pattern as if those inputs had
held from the start, except memory patterns, which carry their state on.
"""
from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..errors import RunError
from ..expr import Call, ExprError, Scope, compile_expr, function, is_expr, resolve
from . import catalogue  # noqa: F401 — registers every kind
from .base import KINDS, MEMORY_STATE, KindSpec, PatternConfig
from .timebase import calendar_of, moment, now, step_length

if TYPE_CHECKING:
    from ..world.store import World

__all__ = ["PatternRuntime", "Ctx", "PatternsView", "parsed_patterns", "key_text"]

#: Deepest nesting of patterns reading patterns (composites, draws in parameters).
_MAX_DEPTH = 24
_PARSED: dict[int, tuple[Any, dict[str, PatternConfig]]] = {}


def parsed_patterns(raw: Mapping[str, Any]) -> dict[str, PatternConfig]:
    """A contract's ``patterns`` validated by their kinds (parsed once per contract object)."""
    hit = _PARSED.get(id(raw))
    if hit is not None and hit[0] is raw:
        return hit[1]
    out = {name: KINDS[spec["kind"]].model.model_validate(spec) for name, spec in raw.items()}
    if len(_PARSED) > 256:
        _PARSED.clear()
    _PARSED[id(raw)] = (raw, out)
    return out


def key_text(value: Any) -> str:
    """A key as text: entities by id, whole numbers without a decimal point."""
    if hasattr(value, "entity_type") and hasattr(value, "id"):
        return str(value.id)
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise ValueError(f"a key is text, a number or an entity, got {value!r}")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class Ctx:
    """What a kind's code reads while it evaluates one pattern for one key at one time."""

    __slots__ = ("rt", "name", "cfg", "spec", "key", "t", "source", "depth")

    def __init__(self, rt: PatternRuntime, name: str, key: str | None, t: float, source: str, depth: int = 0):
        self.rt, self.name, self.key, self.t, self.source, self.depth = rt, name, key, t, source, depth
        self.cfg = rt.configs[name]
        self.spec: KindSpec = KINDS[self.cfg.kind]

    @property
    def world(self) -> World:
        return self.rt.world

    @property
    def clock(self) -> Any:
        return calendar_of(self.rt.world)

    def fail(self, message: str) -> ExprError:
        return ExprError(f"mechanisms.{self.name}: {message}", self.source)

    def param(self, field: str) -> Any:
        """A parameter's value for this key (expressions evaluated once per run)."""
        return self.rt.param(self.name, self.key, field, self.source, self.depth)

    def number(self, field: str, low: float | None = None, high: float | None = None) -> float:
        value = self.param(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise self.fail(f"`{field}` must be a finite number, got {value!r}")
        if (low is not None and value < low) or (high is not None and value > high):
            span = f"from {low:g}" if high is None else (f"up to {high:g}" if low is None
                                                         else f"from {low:g} to {high:g}")
            raise self.fail(f"`{field}` must be {span}, got {value:g}")
        return float(value)

    def optional(self, field: str, low: float | None = None, high: float | None = None) -> float | None:
        return None if getattr(self.cfg, field) is None else self.number(field, low, high)

    def numbers(self, field: str) -> list[float]:
        value = self.param(field)
        if not isinstance(value, list) or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                  and math.isfinite(v) for v in value):
            raise self.fail(f"`{field}` must be a list of numbers, got {value!r}")
        return [float(v) for v in value]

    def stream(self, *parts: Any) -> Any:
        return self.rt.world.seeds.rng("pattern", self.name, self.key or "", *parts)

    def uniform(self, *parts: Any) -> float:
        """A uniform number in (0, 1) fixed by this pattern, key and ``parts`` — the same every time it is asked."""
        u = self.stream("u", *parts).random()
        return min(max(u, 1e-12), 1 - 1e-12)

    def step(self, t: float | None = None) -> int:
        """The step of a random process ``t`` falls in (``every`` clock units each, by default one round)."""
        every = self.every()
        return max(0, math.floor((self.t if t is None else t) / every + 1e-9))

    def every(self) -> float:
        raw = getattr(self.cfg, "every", None)
        return float(raw) if raw is not None else step_length(self.clock)

    def moment(self, t: float | None = None) -> Any:
        return moment(self.clock, self.t if t is None else t)

    def row(self) -> Mapping[str, Any] | None:
        return self.rt.row(self.name, self.key, self.source)

    def path(self, n: int, first: Any, advance: Any) -> Any:
        """Value ``n`` of this pattern's random path: ``first(ctx, rng)`` gives ``(value, state)`` of step 0 and
        ``advance(ctx, rng, state, step)`` each next one, drawing in order from the pattern's own stream."""
        return self.rt.path(self, n, first, advance)

    def operand(self, name: str, key: str | None) -> Any:
        """Another pattern's value at this time, for ``key``."""
        return self.rt.evaluate(name, key, [], self.t, self.source, self.depth + 1)

    def cached(self, label: str, build: Any) -> Any:
        """Something derived once per run from this pattern's fixed parameters (an index of its data)."""
        return self.rt.cached(self.name, self.key, label, build)

    def input(self) -> Any:
        return self.rt.memory_input(self)

    def state(self) -> dict[str, Any] | None:
        entries = (self.rt.world.props.get(MEMORY_STATE) or {}).get(self.name) or {}
        return entries.get(self.key or "")


class PatternsView:
    """``$pattern`` — ``$pattern.winter`` reads a pattern now; ``$pattern.lift($it.price, $it.sku)`` calls one."""

    def __init__(self, runtime: PatternRuntime):
        self._runtime = runtime

    def expr_attr(self, name: str, source: str | None) -> Any:
        return self._runtime.call(name, [], source or "")

    def expr_call(self, name: str, args: Sequence[Any], source: str) -> Any:
        return self._runtime.call(name, list(args), source)


class PatternRuntime:
    """The declared patterns of one world."""

    def __init__(self, world: World):
        self.world = world
        self.configs = parsed_patterns(world.contract.patterns)
        self.view = PatternsView(self)
        #: Read every parameter at its estimate: no draws from its standard error (see :meth:`at_estimates`).
        self.estimates = False
        self._params: dict[tuple[str, str | None, str], Any] = {}
        self._rows: dict[str, dict[str, Mapping[str, Any]]] = {}
        self._keys: dict[str, list[str]] = {}
        self._paths: dict[tuple[str, str], list[Any]] = {}
        self._lock = threading.RLock()

    def bound_to(self, world: World) -> PatternRuntime:
        """This runtime for a copy of its world (a clone of the same run: same contract, seed and inputs), sharing
        everything derived from them — parameters, rows, keys and random paths are the same values for both."""
        copy = PatternRuntime.__new__(PatternRuntime)
        copy.__dict__.update(self.__dict__, world=world)
        copy.view = PatternsView(copy)
        return copy

    # -- reading ------------------------------------------------------------------------

    def call(self, name: str, args: list[Any], source: str) -> Any:
        cfg = self._config(name, source)
        spec = KINDS[cfg.kind]
        names = spec.arg_names(cfg)
        wanted = len(names) + (1 if cfg.keyed else 0)
        stream_key = spec.random and not cfg.keyed and len(args) == len(names) + 1
        if len(args) != wanted and not stream_key:
            raise ExprError(f"$pattern.{name} {_signature(name, names, cfg.keyed, spec.random)}", source)
        key: str | None = None
        if cfg.keyed or stream_key:
            try:
                key = key_text(args[-1])
            except ValueError as exc:
                raise ExprError(f"$pattern.{name}: {exc}", source) from None
            args = args[:-1]
            if cfg.keyed:
                self._check_key(name, key, source)
        return self.evaluate(name, key, args, now(self.world), source)

    def evaluate(self, name: str, key: str | None, args: Sequence[Any], t: float, source: str, depth: int = 0) -> Any:
        """The value of ``name`` for ``key`` at time ``t`` (memory patterns only at the world's own time)."""
        if depth > _MAX_DEPTH:
            raise ExprError(f"patterns read each other more than {_MAX_DEPTH} deep (a cycle through {name}?)", source)
        ctx = Ctx(self, name, key, t, source, depth)
        value = ctx.spec.evaluate(ctx, *args)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise ctx.fail(f"gave {value} (check its parameters)")
            if ctx.cfg.min is not None:
                value = max(ctx.number("min"), value)
            if ctx.cfg.max is not None:
                value = min(ctx.number("max"), value)
        return value

    def values(self, name: str, source: str) -> dict[str, Any]:
        """``{key: value}`` of a keyed pattern read without arguments, over every key."""
        cfg = self._config(name, source)
        if not cfg.keyed:
            raise ExprError(f"$pattern_values('{name}'): '{name}' has no keys; read it as $pattern.{name}", source)
        if KINDS[cfg.kind].arg_names(cfg):
            raise ExprError(f"$pattern_values('{name}'): '{name}' takes arguments, so it has no value by itself",
                            source)
        t = now(self.world)
        return {key: self.evaluate(name, key, [], t, source) for key in self.keys(name, source)}

    def _config(self, name: str, source: str) -> PatternConfig:
        cfg = self.configs.get(name)
        if cfg is None:
            from difflib import get_close_matches

            hint = get_close_matches(name, list(self.configs), n=1)
            known = f"did you mean '{hint[0]}'?" if hint else f"declared: {', '.join(self.configs) or 'none'}"
            raise ExprError(f"$pattern.{name}: no such pattern ({known})", source)
        return cfg

    # -- parameters, keys and rows ---------------------------------------------------------

    def at_estimates(self) -> None:
        """From now on read every parameter at its estimate, forgetting any draw already made — to tell what a fitted
        pattern does, not what one run's draw of it did."""
        self.estimates = True
        self._params.clear()

    def param(self, name: str, key: str | None, field: str, source: str, depth: int = 0) -> Any:
        cache = (name, key, field)
        if cache in self._params:
            return self._params[cache]
        raw = _plain(getattr(self.configs[name], field))
        value = raw
        if _dynamic(raw):
            roots: dict[str, Any] = {"inputs": self.world.inputs, "pattern": _Nested(self, depth), "key": key}
            row = self.row(name, key, source) if self.configs[name].table is not None and key is not None else None
            roots["row"] = row
            try:
                value = resolve(raw, Scope(roots, self.world))
            except ExprError as exc:
                raise ExprError(f"mechanisms.{name}.{field}: {exc.detail}", source) from None
        if field in self.configs[name].uncertainty and not self.estimates:
            value = self._uncertain(name, key, field, value, source, depth)
        self._params[cache] = value
        return value

    def _uncertain(self, name: str, key: str | None, field: str, value: Any, source: str, depth: int) -> Any:
        """``value`` drawn once per run from a normal with the parameter's standard error (elementwise for lists)."""
        raw = self.configs[name].uncertainty[field]
        scope = Scope({"inputs": self.world.inputs, "pattern": _Nested(self, depth), "key": key,
                       "row": self.row(name, key, source) if self.configs[name].table is not None and key is not None
                       else None},
                      self.world)
        try:
            error = resolve(raw, scope)
        except ExprError as exc:
            raise ExprError(f"mechanisms.{name}.uncertainty.{field}: {exc.detail}", source) from None
        rng = self.world.seeds.rng("pattern", name, key or "", "uncertainty", field)
        values, errors = (value, error) if isinstance(value, list) else ([value], [error])
        if not isinstance(errors, list) or len(errors) != len(values) or not all(
                _number(v) for v in values) or not all(_number(e) and e >= 0 for e in errors):
            raise ExprError(f"mechanisms.{name}.uncertainty.{field}: needs one standard error ≥ 0 per value of "
                            f"`{field}`, got {error!r} for {value!r}", source)
        drawn = [v + e * rng.gauss(0.0, 1.0) if e > 0 else v for v, e in zip(values, errors)]
        return drawn if isinstance(value, list) else drawn[0]

    def cached(self, name: str, key: str | None, label: str, build: Any) -> Any:
        """A value derived once per run from a pattern's fixed parameters (an index of its data, a schedule)."""
        slot = (name, key, "#" + label)
        if slot not in self._params:
            self._params[slot] = build()
        return self._params[slot]

    def keys(self, name: str, source: str) -> list[str]:
        """Every key of a keyed pattern now (an entity type's living ids, or the listed keys)."""
        cfg = self.configs[name]
        if isinstance(cfg.keys, str) and cfg.keys in self.world.contract.types:
            return [entity.id for entity in self.world.alive_of(cfg.keys)]
        if name not in self._keys:
            if cfg.keys is None:
                listed: Any = list(self._table(name, source))
            else:
                try:
                    listed = resolve(cfg.keys, Scope({"inputs": self.world.inputs}, self.world))
                except ExprError as exc:
                    raise ExprError(f"mechanisms.{name}.keys: {exc.detail}", source) from None
            if not isinstance(listed, list):
                raise ExprError(f"mechanisms.{name}.keys must give a list, got {listed!r}", source)
            try:
                self._keys[name] = [key_text(item) for item in listed]
            except ValueError as exc:
                raise ExprError(f"mechanisms.{name}.keys: {exc}", source) from None
        return self._keys[name]

    def row(self, name: str, key: str | None, source: str) -> Mapping[str, Any] | None:
        if self.configs[name].table is None or key is None:
            return None
        rows = self._table(name, source)
        if key not in rows:
            shown = ", ".join(list(rows)[:8]) + (" …" if len(rows) > 8 else "")
            raise ExprError(f"mechanisms.{name}: its table has no row for key '{key}' (keys: {shown})", source)
        return rows[key]

    def _table(self, name: str, source: str) -> dict[str, Mapping[str, Any]]:
        if name not in self._rows:
            cfg = self.configs[name]
            try:
                rows = compile_expr(str(cfg.table))(Scope({"inputs": self.world.inputs}, self.world))
            except ExprError as exc:
                raise ExprError(f"mechanisms.{name}.table: {exc.detail}", source) from None
            if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
                raise ExprError(f"mechanisms.{name}.table must give a list of rows, got {type(rows).__name__}", source)
            indexed: dict[str, Mapping[str, Any]] = {}
            for index, row in enumerate(rows):
                if cfg.column not in row:
                    raise ExprError(f"mechanisms.{name}.table: row {index} has no column '{cfg.column}'", source)
                indexed[key_text(row[cfg.column])] = row
            self._rows[name] = indexed
        return self._rows[name]

    def _check_key(self, name: str, key: str, source: str) -> None:
        cfg = self.configs[name]
        if isinstance(cfg.keys, str) and cfg.keys in self.world.contract.types:
            entity = self.world.entities.get(key)
            if entity is None or not self.world.is_a(entity.entity_type, cfg.keys):
                raise ExprError(f"$pattern.{name}: '{key}' is not a {cfg.keys}", source)
        elif cfg.keys is not None and key not in self.keys(name, source):
            raise ExprError(f"$pattern.{name}: '{key}' is not one of its keys", source)
        elif cfg.table is not None:
            self.row(name, key, source)

    # -- random paths ---------------------------------------------------------------------

    def path(self, ctx: Ctx, n: int, first: Any, advance: Any) -> Any:
        slot = (ctx.name, ctx.key or "")
        with self._lock:
            memo = self._paths.get(slot)
            if memo is None:
                rng = ctx.stream("path")
                value, state = first(ctx, rng)
                memo = self._paths[slot] = [rng, state, [value]]
            rng, state, values = memo
            while len(values) <= n:
                value, state = advance(ctx, rng, state, len(values))
                values.append(value)
            memo[1] = state
            return values[n]

    # -- memory ---------------------------------------------------------------------------

    def memory_input(self, ctx: Ctx) -> Any:
        cfg = ctx.cfg
        values: dict[str, Any] = {"key": ctx.key, "row": ctx.row() if cfg.table is not None else None}
        if isinstance(cfg.keys, str) and cfg.keys in self.world.contract.types and ctx.key is not None:
            values["it"] = self.world.entities.get(ctx.key)
        try:
            return compile_expr(str(getattr(cfg, "input")))(self.world.evaluation.scope(**values))  # noqa: B009 — only some pattern kinds declare `input`
        except ExprError as exc:
            raise ExprError(f"mechanisms.{ctx.name}.input: {exc.detail}", ctx.source) from None

    def commit(self) -> None:
        """Carry every memory pattern's state past the round that just ended."""
        names = [name for name, cfg in self.configs.items() if KINDS[cfg.kind].commit is not None]
        if not names:
            return
        t = now(self.world)
        store = dict(self.world.props.get(MEMORY_STATE) or {})
        for name in names:
            where = f"mechanisms.{name}"
            entries = dict(store.get(name) or {})
            keys: list[str | None] = list(self.keys(name, where)) if self.configs[name].keyed else [None]
            for key in keys:
                ctx = Ctx(self, name, key, t, where)
                try:
                    entries[key or ""] = ctx.spec.commit(ctx, ctx.input(), ctx.state())  # type: ignore[misc]
                except ExprError as exc:
                    raise RunError(exc.detail, where) from None
            store[name] = entries
        self.world.set_world(MEMORY_STATE, store)


class _Nested:
    """``$pattern`` inside a parameter: reads draw patterns one level deeper (so a cycle is caught)."""

    def __init__(self, runtime: PatternRuntime, depth: int):
        self._runtime, self._depth = runtime, depth

    def expr_attr(self, name: str, source: str | None) -> Any:
        return self.expr_call(name, [], source or "")

    def expr_call(self, name: str, args: Sequence[Any], source: str) -> Any:
        if self._depth >= _MAX_DEPTH:
            raise ExprError(f"patterns read each other more than {_MAX_DEPTH} deep (a cycle through {name}?)", source)
        runtime = self._runtime
        cfg = runtime._config(name, source)
        key = key_text(args[-1]) if cfg.keyed and args else None
        return runtime.evaluate(name, key, list(args[:-1] if cfg.keyed else args), now(runtime.world), source,
                                self._depth + 1)


def _plain(raw: Any) -> Any:
    """A config value as plain data (nested models as dicts), so parameters inside them can be expressions."""
    if isinstance(raw, BaseModel):
        return raw.model_dump()
    if isinstance(raw, list):
        return [_plain(item) for item in raw]
    if isinstance(raw, dict):
        return {key: _plain(item) for key, item in raw.items()}
    return raw


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _dynamic(raw: Any) -> bool:
    if isinstance(raw, str):
        return is_expr(raw)
    if isinstance(raw, list):
        return any(_dynamic(item) for item in raw)
    if isinstance(raw, dict):
        return any(_dynamic(item) for item in raw.values())
    return False


def _signature(name: str, names: Sequence[str], keyed: bool, random: bool) -> str:
    parts = list(names) + (["key"] if keyed else [])
    shown = f"$pattern.{name}({', '.join(parts)})" if parts else f"$pattern.{name}"
    extra = " (an extra last argument, a key, gives each item its own draws)" if random and not keyed else ""
    return f"is read as {shown}{extra}"


@function("pattern_values(name)", "Every key's value of a keyed pattern now, as {key: value}: "
          "$pattern_values('season').", min_args=1, max_args=1, family="pattern")
def _pattern_values(call: Call) -> Any:
    runtime = getattr(call.scope.world, "patterns", None)
    name = call.arg(0)
    if runtime is None or not isinstance(name, str):
        raise ExprError("$pattern_values(name): name a declared pattern, like $pattern_values('season')", call.source)
    return runtime.values(name, call.source)
