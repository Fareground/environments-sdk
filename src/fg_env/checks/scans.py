"""Scan lint: reading every row of an input table from work that runs once per entity or row, or every entity of a
type from a block that runs once per arrival, so the cost grows with the square of their number.

A `$filter($inputs.sales, $it.sku == $row.sku)` for every population row, or a `$count(call, $it.status == waiting)`
in a block that schedules itself again for every arrival, is invisible at a few dozen and dominates a run at thousands.
Input tables never change during a run, so a table scan repeated per entity or row always has a better form
(`$lookup`); it is reported in events and `each` effects over a type, type hooks, population rows and recurring blocks.
Reading every entity of a type per entity is often the model itself (a random partner, a neighbourhood), so type scans
are reported only in blocks that schedule themselves again through `after`. A def read there counts too when its
body scans. Once-per-round scans are never reported.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..effects.statements import statement_parts
from ..expr import ExprError, compile_expr, is_expr

if TYPE_CHECKING:
    from . import _Checker

__all__ = ["check_scans"]

#: Keys of an effect object that hold nested effect lists.
_NESTED = ("then", "else", "do")
_TABLE_SCAN = re.compile(r"\$([a-z_]+)\(\s*\$inputs\.([A-Za-z_][A-Za-z0-9_]*)")
_TYPE_FIX = ("keep what this needs in a world list or map updated when it changes (a queue of ids: "
             "`$world.queue += $made.id`, then `$world.queue[0]`), or compute it once per round and read that")
_TABLE_FIX = "`$lookup($inputs.{table}, field, value)` finds the matching rows through an index built once per run"


@dataclass(frozen=True)
class _Scan:
    table: str | None  # the input table read, or None for a type
    visited: str
    how: str


@dataclass(frozen=True)
class _Work:
    """Work that repeats per entity, row or arrival: how to name it, and whether type scans count there."""

    each: str
    types: bool = False


class _Scans:
    def __init__(self, checker: _Checker):
        self.checker = checker
        self.c = checker.c
        self.scanners = checker.collection_funcs
        self.tables = {name for name, spec in self.c.inputs.items() if spec.type == "table"}
        self.def_scans: dict[str, list[_Scan]] = {}

    # -- what an expression scans ------------------------------------------------------

    def scans(self, source: str, busy: tuple[str, ...] = ()) -> list[_Scan]:
        """Every scan in expression ``source``, its defs' scans included."""
        try:
            compiled = compile_expr(source)
        except ExprError:
            return []  # the checker reports the syntax error
        found = [_Scan(None, f"every {symbol}", f"${name}({symbol}, …)")
                 for name, symbol in sorted(compiled.calls, key=str)
                 if name in self.scanners and symbol in self.c.types]
        found += [_Scan(table, f"every row of $inputs.{table}", f"${name}($inputs.{table}, …)")
                  for name, table in _TABLE_SCAN.findall(source) if name in self.scanners and table in self.tables]
        for name in sorted((set(compiled.functions) | set(compiled.roots)) & set(self.c.defs)):
            if name in busy:
                continue
            if name not in self.def_scans:
                self.def_scans[name] = self.scans(self.c.defs[name].expr, (*busy, name))
            found += [_Scan(inner.table, inner.visited, f"${name} (defs.{name} uses {inner.how})")
                      for inner in self.def_scans[name]]
        return found

    def report(self, source: Any, path: str, work: _Work) -> None:
        if not isinstance(source, str) or not is_expr(source):
            return
        for scan in self.scans(source):
            if scan.table is None and not work.types:
                continue
            self.checker.warn(path, f"{scan.how} visits {scan.visited} for {work.each}, so the work grows with the "
                                    "square of their number",
                              _TABLE_FIX.format(table=scan.table) if scan.table else _TYPE_FIX)

    def statement(self, source: str, path: str, work: _Work) -> None:
        try:
            _, steps, _, _, right = statement_parts(source)
        except ExprError:
            return  # the checker reports the malformed statement
        self.report(right, path, work)
        for kind, step in steps:
            if kind == "index":
                self.report(step, path, work)

    # -- where work repeats ------------------------------------------------------------

    def effects(self, effects: Any, path: str, work: _Work | None, seen: set[str]) -> None:
        """Walk an effect list; expressions are reported when ``work`` names the repeated work around them."""
        items = [effects] if isinstance(effects, (str, dict)) else effects if isinstance(effects, list) else []
        for index, effect in enumerate(items):
            where = f"{path}[{index}]"
            if isinstance(effect, str):
                if work is not None:
                    self.statement(effect, where, work)
                continue
            if not isinstance(effect, dict):
                continue
            source = effect.get("each")
            inner = _Work(f"each {source}", work.types if work else False) \
                if isinstance(source, str) and source in self.c.types else work
            for key, raw in effect.items():
                if key in _NESTED:
                    self.effects(raw, f"{where}.{key}", inner if key == "do" else work, seen)
                    continue
                around = inner if key == "where" else work
                if around is not None:
                    for leaf_path, leaf in _leaves(raw, f"{where}.{key}"):
                        self.report(leaf, leaf_path, around)
            block = effect.get("block")
            if work is not None and isinstance(block, str) and block in self.c.blocks and block not in seen:
                self.effects(self.c.blocks[block].do, f"blocks.{block}.do", work, seen | {block})

    def recurring_blocks(self) -> list[str]:
        """Blocks that schedule themselves again through `after` (directly or through other blocks)."""
        edges = {name: list(_calls(spec.do, False)) for name, spec in self.c.blocks.items()}

        def returns(start: str) -> bool:
            stack = list(edges[start])
            visited: set[tuple[str, bool]] = set()
            while stack:
                node, timed = stack.pop()
                if node == start and timed:
                    return True
                if (node, timed) in visited or node not in edges:
                    continue
                visited.add((node, timed))
                stack += [(callee, timed or late) for callee, late in edges[node]]
            return False

        return [name for name in self.c.blocks if returns(name)]

    def run(self) -> None:
        c = self.c
        for index, event in enumerate(c.events):
            work = _Work(f"each {event.each}") if isinstance(event.each, str) and event.each in c.types else None
            if work is not None:
                self.report(event.where, f"events[{index}].where", work)
            self.effects(event.do, f"events[{index}].do", work, set())
        for name, action in c.actions.items():
            self.effects(action.do, f"actions.{name}.do", None, set())
        for name, spec in c.types.items():
            for hook in ("on_create", "on_remove"):
                self.effects(getattr(spec, hook), f"types.{name}.{hook}", _Work(f"each {name} {hook[3:]}d"), set())
        for name in self.recurring_blocks():
            self.effects(c.blocks[name].do, f"blocks.{name}.do",
                         _Work(f"each run of block {name} (it schedules itself again)", types=True), {name})
        for index, group in enumerate(c.population):
            path, work = f"population[{index}]", _Work(f"each row of population[{index}]")
            for key in ("where", "weight"):
                self.report(getattr(group, key), f"{path}.{key}", work)
            for leaf_path, leaf in _leaves(group.props, f"{path}.props"):
                self.report(leaf, leaf_path, work)
            for member_index, members in enumerate(group.members):
                for leaf_path, leaf in _leaves(members.props, f"{path}.members[{member_index}].props"):
                    self.report(leaf, leaf_path, _Work(f"each member of population[{index}]"))


def _leaves(raw: Any, path: str) -> Iterable[tuple[str, Any]]:
    if isinstance(raw, dict):
        for key, item in raw.items():
            yield from _leaves(item, f"{path}.{key}")
    elif isinstance(raw, list):
        for index, item in enumerate(raw):
            yield from _leaves(item, f"{path}[{index}]")
    else:
        yield path, raw


def _calls(effects: Any, timed: bool) -> Iterable[tuple[str, bool]]:
    """``(block, reached through after)`` for every block an effect list runs."""
    items = [effects] if isinstance(effects, dict) else effects if isinstance(effects, list) else []
    for effect in items:
        if not isinstance(effect, dict):
            continue
        if isinstance(effect.get("block"), str):
            yield effect["block"], timed
        for key in _NESTED:
            if key in effect:
                yield from _calls(effect[key], timed or "after" in effect)


def check_scans(checker: _Checker) -> None:
    """Warn about scans that repeat for every entity, row or arrival (see the module notes)."""
    _Scans(checker).run()
