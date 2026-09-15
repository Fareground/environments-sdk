"""Static checks for ``patterns``: parameters stay fixed for a run, references resolve, reads match each kind's shape."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Set

from ..expr import ExprError, compile_expr, is_expr
from . import timebase as tb
from .base import KINDS, PatternConfig
from .compose import operand_names
from .expand import validated

if TYPE_CHECKING:
    from ..check import _Checker

__all__ = ["check_patterns", "check_pattern_call", "configs"]

#: Kinds a parameter may read through $pattern: their values are fixed for a run.
_FIXED = ("draw", "segments")


def configs(contract: Any) -> Dict[str, PatternConfig]:
    out = {}
    for name, spec in contract.patterns.items():
        cfg, _ = validated(name, spec)
        if cfg is not None:
            out[name] = cfg
    return out


def check_patterns(checker: "_Checker", base: FrozenSet[str]) -> None:
    declared = configs(checker.c)
    for name, cfg in declared.items():
        path = f"patterns.{name}"
        kind = KINDS[cfg.kind]
        _keys(checker, cfg, path)
        roots = {"inputs", "pattern"} | ({"key"} if cfg.keyed else set()) | ({"row"} if cfg.table is not None else set())
        fields = [*kind.params, "min", "max"]
        for field in fields:
            _fixed(checker, declared, getattr(cfg, field, None), f"{path}.{field}", roots)
        for field, error in cfg.uncertainty.items():
            if field not in kind.params:
                checker.error(f"{path}.uncertainty.{field}", f"'{field}' is not a parameter of a {cfg.kind} pattern",
                              f"parameters: {', '.join(kind.params) or 'none'}")
            _fixed(checker, declared, error, f"{path}.uncertainty.{field}", roots)
        if kind.shape == "memory":
            types = {"it": {cfg.keys}} if isinstance(cfg.keys, str) and cfg.keys in checker.c.types else {}
            extra = ({"key"} if cfg.keyed else set()) | ({"row"} if cfg.table is not None else set()) | set(types)
            checker.expr(getattr(cfg, "input"), f"{path}.input", set(base) | extra, types)
        if kind.shape == "composite":
            _operands(checker, declared, name, cfg, path)
        if cfg.kind == "cross_price" and cfg.keys is None:
            checker.error(path, "a cross_price pattern needs `keys` (the items whose prices it is called with)",
                          'add "keys": ["economy", "premium"] or an entity type')
        _time(checker, cfg, path)
        if cfg.fit is not None:
            _fit(checker, declared, name, cfg, path)
    _cycles(checker, declared)


def _keys(checker: "_Checker", cfg: PatternConfig, path: str) -> None:
    if isinstance(cfg.keys, str):
        if is_expr(cfg.keys):
            checker.expr(cfg.keys, f"{path}.keys", {"inputs"})
        elif cfg.keys not in checker.c.types:
            checker.error(f"{path}.keys", f"'{cfg.keys}' is not a declared type",
                          checker._suggest(cfg.keys, checker.c.types) or "give an entity type, a list, or an expression over $inputs")
    if cfg.table is not None:
        checker.expr(cfg.table, f"{path}.table", {"inputs"})
        if cfg.column is None:
            checker.error(f"{path}.column", "a table needs `column`: the column holding each row's key", 'add "column": "sku"')
    elif cfg.column is not None:
        checker.error(f"{path}.column", "`column` names the key column of a `table`", "add `table` or remove `column`")


def _fixed(checker: "_Checker", declared: Dict[str, PatternConfig], raw: Any, path: str, roots: Set[str]) -> None:
    """A parameter: a literal, or an expression over the roots fixed for a run, calling nothing random."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            _fixed(checker, declared, item, f"{path}[{index}]", roots)
        return
    if isinstance(raw, dict):
        for key, item in raw.items():
            _fixed(checker, declared, item, f"{path}.{key}", roots)
        return
    if not (isinstance(raw, str) and is_expr(raw)):
        return
    try:
        compiled = compile_expr(raw)
    except ExprError as exc:
        checker.error(path, exc.detail, f"expression: {raw}")
        return
    for root in sorted(compiled.roots - roots):
        checker.error(path, f"${root} is not available in a pattern parameter",
                      "parameters are fixed for a run: read $inputs" + (", $key" if "key" in roots else "")
                      + (", $row" if "row" in roots else "") + " or a draw pattern; state belongs in an argument or a memory input")
        return
    from ..describe.walk import random_functions  # imported late: describe imports the API, which imports the checker

    random = sorted(compiled.functions & random_functions())
    if random:
        checker.error(path, f"${random[0]} draws from the shared stream, so the parameter would change with call order",
                      "declare a draw pattern and read it: $pattern.<name>")
    for chain in compiled.paths:
        if chain[0] == "pattern" and len(chain) > 1:
            other = declared.get(chain[1])
            if other is not None and other.kind not in _FIXED:
                checker.error(path, f"$pattern.{chain[1]} changes during a run, so a parameter cannot read it",
                              "a parameter may read draw and segments patterns; combine others with a product or sum")
    checker.expr(raw, path, roots)


def _operands(checker: "_Checker", declared: Dict[str, PatternConfig], name: str, cfg: Any, path: str) -> None:
    for index, (item, other_name) in enumerate(zip(cfg.of, operand_names(cfg))):
        at = f"{path}.of[{index}]"
        other = declared.get(other_name)
        if other is None:
            checker.error(at, f"'{other_name}' is not a declared pattern", checker._suggest(other_name, declared))
            continue
        if other_name == name:
            checker.error(at, "a pattern cannot combine itself")
            continue
        if KINDS[other.kind].arg_names(other):
            checker.error(at, f"'{other_name}' is called with {', '.join(KINDS[other.kind].arg_names(other))}, so it "
                              "cannot be combined", "combine patterns that are read without arguments")
        if isinstance(item, str):
            if other.keyed and not cfg.keyed:
                checker.error(at, f"'{other_name}' is keyed but '{name}' is not, so it has no key to pass on",
                              f'give "{name}" the same keys, or write {{"pattern": "{other_name}", "key": "..."}}')
        else:
            roots = {"inputs"} | ({"key"} if cfg.keyed else set()) | ({"row"} if cfg.table is not None else set())
            checker.expr(item.key, f"{at}.key", roots)
            if not other.keyed:
                checker.error(f"{at}.key", f"'{other_name}' has no keys", "name it on its own")


def _time(checker: "_Checker", cfg: Any, path: str) -> None:
    clock = checker.c.clock
    if cfg.kind == "calendar" and (not clock.start or tb.unit_days(clock) is None):
        checker.error(path, "calendar effects need clock.start and a calendar clock.unit (day, week, hour …)",
                      'set clock.start (e.g. "2025-01-06") and clock.unit')
    period = getattr(cfg, "period", None)
    if cfg.kind in ("seasonal",) and isinstance(period, str) and tb.unit_days(clock) is None:
        checker.error(f"{path}.period", f"a '{period}' period needs a calendar clock.unit, not '{clock.unit}'",
                      "set clock.unit (week, day …) or give the period as a number of rounds")
    if cfg.kind == "weather" and tb.unit_days(clock) is None:
        checker.error(path, f"weather follows the year, which needs a calendar clock.unit, not '{clock.unit}'",
                      "set clock.unit (week, day …)")
    for field in ("origin", "start", "at", "window"):
        value = getattr(cfg, field, None)
        items = value if isinstance(value, list) else [value]
        for index, item in enumerate(items):
            if isinstance(item, str) and not is_expr(item):
                at = f"{path}.{field}" + (f"[{index}]" if isinstance(value, list) else "")
                try:
                    tb.to_t(clock, item)
                except ValueError as exc:
                    checker.error(at, str(exc), "write clock units from round 1 or an ISO date with clock.start set")


def _fit(checker: "_Checker", declared: Dict[str, PatternConfig], name: str, cfg: PatternConfig, path: str) -> None:
    fit = cfg.fit
    assert fit is not None
    checker.expr(fit.data, f"{path}.fit.data", {"inputs"})
    checker.expr(fit.where, f"{path}.fit.where", {"inputs", "row"})
    for index, other in enumerate(fit.adjust):
        if other not in declared or other == name:
            checker.error(f"{path}.fit.adjust[{index}]", f"'{other}' is not another declared pattern",
                          checker._suggest(other, declared))
    if isinstance(fit.x, dict):
        names = operand_names(cfg) if KINDS[cfg.kind].shape == "composite" else []
        for factor in fit.x:
            if factor not in names:
                checker.error(f"{path}.fit.x.{factor}", f"'{factor}' is not a factor of '{name}'",
                              f"factors: {', '.join(names) or 'none'}")


def _cycles(checker: "_Checker", declared: Dict[str, PatternConfig]) -> None:
    edges: Dict[str, List[str]] = {name: operand_names(cfg) if KINDS[cfg.kind].shape == "composite" else []
                                   for name, cfg in declared.items()}
    state: Dict[str, int] = {}

    def visit(name: str, trail: List[str]) -> Optional[List[str]]:
        state[name] = 1
        for other in edges.get(name, []):
            if state.get(other) == 1:
                return trail + [name, other]
            if other in edges and state.get(other) is None:
                found = visit(other, trail + [name])
                if found:
                    return found
        state[name] = 2
        return None

    for name in edges:
        if state.get(name) is None:
            cycle = visit(name, [])
            if cycle:
                checker.error(f"patterns.{cycle[0]}.of", "patterns combine each other in a cycle: " + " → ".join(cycle))
                return


def check_pattern_call(checker: "_Checker", compiled: Any, path: str) -> None:
    """Reads (``$pattern.x``) and calls (``$pattern.x(a, key)``) of patterns in one expression."""
    declared = configs(checker.c)
    called = {name for root, name, _ in compiled.methods if root == "pattern"}
    for root, name, count in compiled.methods:
        if root != "pattern":
            checker.error(path, f"${root}.{name}(…): only $pattern members can be called", f"in `{compiled.source}`")
            continue
        cfg = declared.get(name)
        if cfg is None:
            continue
        kind = KINDS[cfg.kind]
        wanted = len(kind.arg_names(cfg)) + (1 if cfg.keyed else 0)
        if count == wanted or (kind.random and not cfg.keyed and count == wanted + 1):
            continue
        parts = [*kind.arg_names(cfg), *(["key"] if cfg.keyed else [])]
        checker.error(path, f"$pattern.{name} takes {len(parts)} argument(s): ({', '.join(parts)}), got {count}",
                      f"write $pattern.{name}({', '.join(parts)})" if parts else f"read it as $pattern.{name}")
    for chain in compiled.paths:
        if chain[0] != "pattern" or len(chain) < 2:
            continue
        name = chain[1]
        cfg = declared.get(name)
        if cfg is None:
            if name not in checker.c.patterns:
                checker.error(path, f"$pattern.{name}: no such pattern",
                              checker._suggest(name, declared) or "declare it under `patterns`")
            continue
        if name in called:
            continue
        parts = [*KINDS[cfg.kind].arg_names(cfg), *(["key"] if cfg.keyed else [])]
        if parts:
            checker.error(path, f"$pattern.{name} is read with ({', '.join(parts)})",
                          f"write $pattern.{name}({', '.join(parts)}) — in `{compiled.source}`")
