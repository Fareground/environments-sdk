"""Dual evaluation: every expression evaluated by the reference evaluator (:mod:`expr_oracle`) and by the compiled
language, from the same state, with every difference recorded.

Inside :func:`dual_evaluation`, a top-level evaluation first runs the oracle (nested evaluations — def bodies, record
visibility rules — run the oracle too), notes what it gave and what it left behind, puts the state back, then runs
the compiled expression (nested evaluations compiled too). Compared: the value (types included: participant text,
whole vs decimal numbers, map key order), or the error (type and message); the work budget afterwards; every random
stream's state and the draw count; the def cache. The compiled result is what the run goes on with.

Differences are collected, not raised inside the engine (code under test may catch errors), so a test asserts
:data:`MISMATCHES` stays empty.
"""
from __future__ import annotations

import copy
import gc
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Tuple

from expr_oracle import compile_oracle

from fg_env.expr import compile as expr_compile
from fg_env.expr.base import _BUDGET, ExprError
from fg_env.mechanisms import turn_order
from fg_env.seeds import DrawSite
from fg_env.stdlib import tables

#: Every difference found: ``(expression, what differed)``.
MISMATCHES: List[Tuple[str, str]] = []
_ORIGINAL = expr_compile.Expr.__call__
_MODE = threading.local()
_ORACLES: Dict[str, Any] = {}
_BUDGET_FIELDS = ("hold", "used", "limit", "cap", "label", "shared")
_UNSET = object()
#: What a world's pattern runtime derives once and keeps (parameters, rows, keys, random paths).
_PATTERN_CACHES = ("_params", "_rows", "_keys", "_paths")


def _oracle(source: str) -> Any:
    found = _ORACLES.get(source)
    if found is None:
        found = _ORACLES[source] = compile_oracle(source)
    return found


def _outcome(run: Callable[[], Any]) -> Tuple[str, Any]:
    try:
        return "value", run()
    except Exception as exc:  # noqa: BLE001 — every error is part of the outcome compared
        return "error", exc


def _streams(world: Any) -> List[Any]:
    """Every random stream an evaluation over ``world`` may draw from."""
    found: List[Any] = []
    candidates = [world.__dict__.get("rng"), world.__dict__.get("_rng")]
    if hasattr(world, "_here"):
        candidates += [getattr(world._here(), "rng", None), getattr(world._local, "rng", None)]
    for rng in candidates:
        if isinstance(rng, DrawSite):
            rng = rng.stream
        if rng is not None and hasattr(rng, "getstate") and not any(rng is known for known in found):
            found.append(rng)
    return found


def _capture(world: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {"budget": tuple(getattr(_BUDGET, name) for name in _BUDGET_FIELDS)}
    if world is None:
        return state
    state["streams"] = [(rng, rng.getstate()) for rng in _streams(world)]
    if hasattr(world, "_here"):
        local = world._here()
        site = getattr(local, "rng", None)
        if isinstance(site, DrawSite):  # a draw site opens at its first draw, counting it and journaling the count
            state["site"] = (site, site.stream, dict(world.firings), world.journal.mark())
        state["counters"] = (local, getattr(local, "draws", _UNSET), getattr(local, "depth", _UNSET))
        state["defs"] = (dict(world._def_cache), world._def_cache_state)
        # Caches that evaluate expressions or charge work when they miss: both evaluators start from the same ones.
        orders = turn_order._ORDERS.get(world)
        social = world.__dict__.get("_social_cache")
        state["caches"] = (None if orders is None else dict(orders),
                           None if social is None else {k: dict(v) if isinstance(v, dict) else v for k, v in social.items()})
        indexes = tables._INDEXES.get(world)
        state["indexes"] = None if indexes is None else dict(indexes)
        patterns = world.__dict__.get("patterns")
        state["patterns"] = None if patterns is None else (patterns, {
            name: {k: _pattern_entry(v) for k, v in getattr(patterns, name).items()} for name in _PATTERN_CACHES})
    return state


def _restore(world: Any, state: Dict[str, Any]) -> None:
    for name, value in zip(_BUDGET_FIELDS, state["budget"]):
        setattr(_BUDGET, name, value)
    if world is None:
        return
    if "site" in state:
        site, site.stream, firings, mark = state["site"]
        world.firings.clear()
        world.firings.update(firings)
        del world.journal._undo[mark:]
    for rng, saved in state["streams"]:
        rng.setstate(saved)
    if "counters" in state:
        local, draws, depth = state["counters"]
        for name, value in (("draws", draws), ("depth", depth)):
            if value is _UNSET:
                if hasattr(local, name):
                    delattr(local, name)
            else:
                setattr(local, name, value)
        world._def_cache, world._def_cache_state = dict(state["defs"][0]), state["defs"][1]
        orders, social = state["caches"]
        if orders is None:
            turn_order._ORDERS.pop(world, None)
        else:
            turn_order._ORDERS[world] = dict(orders)
        if social is None:
            world.__dict__.pop("_social_cache", None)
        else:
            world.__dict__["_social_cache"] = {k: dict(v) if isinstance(v, dict) else v for k, v in social.items()}
        if state["indexes"] is None:
            tables._INDEXES.pop(world, None)
        else:
            tables._INDEXES[world] = dict(state["indexes"])
        if state["patterns"] is not None:
            patterns, saved = state["patterns"]
            for name, entries in saved.items():
                setattr(patterns, name, {k: _pattern_entry(v, restore=True) for k, v in entries.items()})


def _pattern_entry(entry: Any, restore: bool = False) -> Any:
    """A copy of one pattern cache entry that shares nothing mutable with it. A random path is ``[rng, state, values]``
    with the stream advanced and the values appended in place, so its stream state is saved (and set back) too."""
    if isinstance(entry, list) and len(entry) == 3 and hasattr(entry[0], "getstate"):
        rng, state, values = entry
        if restore:
            rng, saved = state
            rng.setstate(saved)
            return [rng, copy.deepcopy(values[0]), list(values[1])]
        return [rng, (rng, rng.getstate()), (copy.deepcopy(state), list(values))]
    return list(entry) if isinstance(entry, list) else entry  # parameters, rows and keys are built once, never changed


def same(a: Any, b: Any) -> bool:
    """Equal values of the same types, all the way down (entities by identity, decimals by representation)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return repr(a) == repr(b)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return [type(k) for k in a] == [type(k) for k in b] and list(a) == list(b) \
            and all(same(a[k], b[k]) for k in a)
    if hasattr(a, "entity_type"):
        return a is b
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001 — incomparable values compare by identity
        return a is b


def _same_outcome(old: Tuple[str, Any], new: Tuple[str, Any]) -> bool:
    if old[0] != new[0]:
        return False
    if old[0] == "value":
        return same(old[1], new[1])
    a, b = old[1], new[1]
    if type(a) is not type(b) or str(a) != str(b):
        return False
    return not isinstance(a, ExprError) or (a.detail, a.source) == (b.detail, b.source)


def _left_behind(state: Dict[str, Any]) -> Tuple[Any, ...]:
    streams = tuple(saved for _, saved in state.get("streams", []))
    counters = state.get("counters", (None, None, None))[1:]
    defs = state.get("defs")
    return state["budget"], streams, counters, (sorted(map(repr, defs[0].items())), defs[1]) if defs else None


def _dual_call(self: Any, scope: Any) -> Any:
    mode = getattr(_MODE, "mode", None)
    if mode == "new":
        return _ORIGINAL(self, scope)
    if mode == "old":
        return _oracle(self.source)(scope)
    world = getattr(scope, "world", None)
    # A garbage collection between or inside the two evaluations may run finalizers (a game state closing its run)
    # that evaluate and charge this thread's budget: it would leave the two evaluations different states to start from.
    collecting = gc.isenabled()
    gc.disable()
    before = _capture(world)
    try:
        _MODE.mode = "old"
        old = _outcome(lambda: _oracle(self.source)(scope))
        old_after = _capture(world)
        _restore(world, before)
        _MODE.mode = "new"
        new = _outcome(lambda: _ORIGINAL(self, scope))
        new_after = _capture(world)
    finally:
        _MODE.mode = None
        if collecting:
            gc.enable()
    if not _same_outcome(old, new):
        MISMATCHES.append((self.source, f"outcome: oracle {old!r}, compiled {new!r}"))
    elif _left_behind(old_after) != _left_behind(new_after):
        differ = [(part, a, b) for part, a, b in zip(("budget", "streams", "counters", "defs"),
                                                     _left_behind(old_after), _left_behind(new_after)) if a != b]
        MISMATCHES.append((self.source, "state left behind differs in " + "; ".join(
            f"{part}: oracle {str(a)[:300]}, compiled {str(b)[:300]}" for part, a, b in differ)))
    if new[0] == "error":
        raise new[1]
    return new[1]


@contextmanager
def dual_evaluation() -> Iterator[List[Tuple[str, str]]]:
    """Inside the block every expression is evaluated both ways; yields the list differences are added to."""
    expr_compile.Expr.__call__ = _dual_call  # type: ignore[method-assign]
    try:
        yield MISMATCHES
    finally:
        expr_compile.Expr.__call__ = _ORIGINAL  # type: ignore[method-assign]
