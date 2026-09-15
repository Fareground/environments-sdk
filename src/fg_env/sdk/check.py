"""Static contract checking: every problem found at once, each with its path and a fix.

Beyond structure, the checker compiles every expression and template, confirms that
referenced types, properties, params, records, relations, stages, views, metrics and
inputs exist, and that each expression only uses roots available where it is written.

The checker's sections live beside it: effects (:mod:`.check_effects`), the world model
(:mod:`.check_world`), actions, stages and views (:mod:`.check_actions`), and events, policies,
measures, defs, arms and calibration (:mod:`.check_rules`).
"""
from __future__ import annotations

import copy
from difflib import get_close_matches
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from pydantic import ValidationError

from . import contract as C
from .check_actions import ActionChecks
from .check_inventory import check_inventory
from .check_effects import EffectChecks
from .check_roots import BASE, ENTITY_FIELDS, Types
from .check_rules import RuleChecks
from .check_scans import check_scans
from .check_world import WorldChecks
from .contract import Contract
from .check_state import check_feeds, check_hooks, check_physics_state, check_relation_fields
from .errors import ContractError, Issue
from .expr import FUNCTIONS, ExprError, compile_expr, is_expr
from .expr_calls import suggest_function
from .parse_errors import validation_issues
from .patterns.check import check_pattern_call, check_patterns
from .returns import check_game
from .template import compile_template, quoted_placeholders
from .world import prop_type
from .assets.checks import check_assets

__all__ = ["parse_contract", "check_contract"]


#: Collection functions whose first parameter is not spelled ``items``.
_OTHER_COLLECTION_FUNCS = frozenset({"first", "last"})


def _collection_funcs() -> Set[str]:
    """Functions whose first argument is a collection: a bare word there must be a type or record."""
    return {name for name, spec in FUNCTIONS.items()
            if spec.signature.split("(", 1)[1].startswith("items")} | _OTHER_COLLECTION_FUNCS


#: Bare words an author may mean as "no value"; in expressions they are plain text.
_NULL_WORDS = frozenset({"none", "None", "nil", "undefined", "Null", "NULL", "empty"})


def parse_contract(data: Any) -> Contract:
    """Validate structure. Raises :class:`ContractError` with every structural problem."""
    if isinstance(data, Contract):
        return data
    if not isinstance(data, Mapping):
        raise ContractError([Issue("(contract)", f"a contract is a JSON object, got {type(data).__name__}")])
    from .mechanisms import expand_mechanisms

    source = copy.deepcopy(dict(data))
    expanded, mechanism_issues = expand_mechanisms(source)
    if mechanism_issues:
        raise ContractError(_dedupe(mechanism_issues))
    from .patterns.expand import expand_patterns

    expanded, pattern_issues = expand_patterns(expanded)
    if pattern_issues:
        raise ContractError(_dedupe(pattern_issues))
    try:
        contract = Contract.model_validate(expanded)
        contract._source = source
        return contract
    except ValidationError as exc:
        raise ContractError(_dedupe(validation_issues(exc))) from None


def _dedupe(issues: Iterable[Issue]) -> List[Issue]:
    seen, out = set(), []
    for issue in issues:
        key = (issue.path, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def check_contract(contract: Contract) -> List[Issue]:
    """All semantic errors and warnings (errors first)."""
    checker = _Checker(contract)
    checker.run()
    issues = _dedupe(checker.issues)
    return [i for i in issues if i.severity == "error"] + [i for i in issues if i.severity != "error"]


class _Checker(EffectChecks, WorldChecks, ActionChecks, RuleChecks):
    def __init__(self, contract: Contract):
        self.c = contract
        self.issues: List[Issue] = []
        self.type_props: Dict[str, Set[str]] = {t: set(contract.props_of(t)) for t in contract.types}
        self.agents = contract.agent_types()
        words: Set[str] = set(contract.types) | set(contract.records) | set(contract.relations) | set(contract.actions)
        words |= {s.name for s in contract.stage_list()} | set(contract.metrics) | set(contract.policies) | set(contract.arms)
        for kind in contract.types:
            for spec in contract.props_of(kind).values():
                words |= {str(v) for v in spec.values or []}
        for action in contract.actions.values():
            for param in action.params.values():
                if isinstance(param.values, list):
                    words |= {str(v) for v in param.values}
        for input_spec in contract.inputs.values():
            words |= {str(v) for v in input_spec.values or []}
        self.known_words = words
        self.stage_names = [s.name for s in contract.stage_list()]
        self.collection_funcs = _collection_funcs()

    # -- reporting -----------------------------------------------------------------

    def error(self, path: str, message: str, fix: Optional[str] = None) -> None:
        self.issues.append(Issue(path, message, fix))

    def warn(self, path: str, message: str, fix: Optional[str] = None) -> None:
        self.issues.append(Issue(path, message, fix, "warning"))

    def _suggest(self, name: str, options: Iterable[str]) -> Optional[str]:
        hint = get_close_matches(name, list(options), n=1)
        return f"did you mean '{hint[0]}'?" if hint else None

    def _hint(self, name: str, options: Iterable[str], what: str) -> str:
        """Did-you-mean when a name is close, otherwise the names there are to choose from."""
        listed = list(options)
        return self._suggest(name, listed) or (f"{what}: {', '.join(listed)}" if listed else f"no {what} declared")

    def _type(self, name: Optional[str], path: str, agent: bool = False) -> bool:
        if name is None:
            return False
        if name not in self.c.types:
            self.error(path, f"'{name}' is not a declared type", self._suggest(name, self.c.types)
                       or f"types: {', '.join(self.c.types)}")
            return False
        if agent and not self.c.is_agent(name):
            self.error(path, f"'{name}' is not an agent type", f"set types.{name}.agent: true")
            return False
        return True

    # -- expressions ---------------------------------------------------------------

    def expr(self, source: Any, path: str, roots: Iterable[str], types: Optional[Types] = None,
             params: Optional[Mapping[str, C.ParamSpec]] = None) -> None:
        if not isinstance(source, str):
            return
        if not is_expr(source) and not source.strip():
            self.error(path, "expression is empty")
            return
        try:
            compiled = compile_expr(source)
        except ExprError as exc:
            self.error(path, exc.detail, f"expression: {source}")
            return
        self._refs(compiled, path, set(roots), types or {}, params or {})

    def value(self, raw: Any, path: str, roots: Iterable[str], types: Optional[Types] = None,
              params: Optional[Mapping[str, C.ParamSpec]] = None) -> None:
        """A literal, a template text, or an expression (deeply, for lists and objects)."""
        if isinstance(raw, str) and "{$" in raw:
            self.template(raw, path, None, roots, types, params)
        elif isinstance(raw, str) and is_expr(raw):
            self.expr(raw, path, roots, types, params)
        elif isinstance(raw, list):
            for i, item in enumerate(raw):
                self.value(item, f"{path}[{i}]", roots, types, params)
        elif isinstance(raw, dict):
            for key, item in raw.items():
                self.value(item, f"{path}.{key}", roots, types, params)

    def template(self, source: Optional[str], path: str, subject: Optional[str], roots: Iterable[str],
                 types: Optional[Types] = None, params: Optional[Mapping[str, C.ParamSpec]] = None) -> None:
        if source is None:
            return
        try:
            compiled = compile_template(source, subject)
        except ExprError as exc:
            self.error(path, exc.detail, f"template: {source}")
            return
        for placeholder in quoted_placeholders(source):
            self.warn(path, f"wraps {placeholder} in «»: participant text already reads inside «», so this shows ««…»», "
                            "and other text in «» reads as written by participants", f"write {placeholder} without the «»")
        for expr in compiled.expressions:
            self._refs(expr, path, set(roots), types or {}, params or {})

    def _refs(self, compiled: Any, path: str, roots: Set[str], types: Types,
              params: Mapping[str, C.ParamSpec]) -> None:
        for root in compiled.roots:
            if root not in roots and not (root in self.c.defs and not self.c.defs[root].args):
                available = ", ".join(f"${r}" for r in sorted(roots))
                self.error(path, f"${root} is not available here", f"available: {available} — in `{compiled.source}`")
        for name, symbol in compiled.calls:
            if name in self.collection_funcs and symbol is not None and symbol not in self.c.types:
                if symbol in self.c.records or name in ("choice", "min", "max"):
                    continue
                self.error(path, f"${name}({symbol}, …): '{symbol}' is not a declared type",
                           self._suggest(symbol, self.c.types) or f"types: {', '.join(self.c.types)}")
            if name in ("empty", "random_empty") and symbol is not None and symbol not in self.c.types:
                self.error(path, f"${name}({symbol}): '{symbol}' is not a declared type",
                           self._suggest(symbol, self.c.types) or f"types: {', '.join(self.c.types)}")
            if name == "layer" and symbol is not None:
                layers = self.c.space.layers if self.c.space is not None else {}
                if symbol not in layers:
                    self.error(path, f"$layer({symbol}, …): '{symbol}' is not a declared layer",
                               self._suggest(symbol, layers) or "declare it under space.layers")
            if name == "records" and symbol is not None and symbol not in self.c.records:
                self.error(path, f"$records({symbol}): '{symbol}' is not a declared record",
                           self._suggest(symbol, self.c.records))
        check_pattern_call(self, compiled, path)
        for name, signature in getattr(compiled, "arity_errors", ()):
            if name not in self.c.defs:
                self.error(path, f"wrong number of arguments: ${signature}", f"in `{compiled.source}`")
        for name in compiled.functions:
            if name not in FUNCTIONS and name not in self.c.defs:
                hint = suggest_function(name, list(FUNCTIONS) + list(self.c.defs))
                self.error(path, f"unknown function ${name}",
                           (f"did you mean ${hint}?" if hint else "declare it under `defs`") + f" — in `{compiled.source}`")
        for chain, word in compiled.comparisons:
            self._compare(self._spec_for(chain, types, params), chain, word, path, compiled.source)
        for _, symbol, chain, word in compiled.item_comparisons:
            if symbol in self.c.types and len(chain) == 2:
                spec = self.c.props_of(symbol).get(chain[1])
                self._compare((spec.values, prop_type(spec)) if spec else None, chain, word, path, compiled.source)
        actor_props: Set[str] = set()
        for kind in types.get("actor", ()):
            actor_props |= self.type_props.get(kind, set())
        for word in compiled.symbols:
            if word in actor_props and word not in self.known_words:
                self.warn(path, f"bare word '{word}' is the text '{word}'", f"did you mean $actor.{word}? — in `{compiled.source}`")
        for chain in compiled.paths:
            self._chain(chain, path, types, params, compiled.source)
        for _, symbol, chain in compiled.item_paths:
            if symbol in self.c.types and len(chain) > 1:
                self._prop({symbol}, chain[1], path, compiled.source, "it")

    def _chain(self, chain: Tuple[str, ...], path: str, types: Types, params: Mapping[str, C.ParamSpec],
               source: str) -> None:
        root, fields = chain[0], chain[1:]
        if not fields:
            return
        first = fields[0]
        if root in types:
            self._prop(types[root], first, path, source, root)
        elif root == "params" and params:
            if first not in params:
                self.error(path, f"$params.{first}: no such parameter",
                           self._suggest(first, params) or f"parameters: {', '.join(params) or 'none'}")
            elif params[first].type == "entity" and len(fields) > 1 and params[first].of in self.c.types:
                self._prop({params[first].of}, fields[1], path, source, f"params.{first}")
        elif root == "world":
            if first not in self.c.world:
                self.error(path, f"$world.{first}: no such world property",
                           self._suggest(first, self.c.world) or "declare it under `world`")
        elif root == "inputs":
            if first not in self.c.inputs:
                self.error(path, f"$inputs.{first}: no such input",
                           self._suggest(first, self.c.inputs) or "declare it under `inputs`")
        elif root == "physics":
            known = set(self.c.physics.vars) | set(self.c.physics.params) | set(self.c.physics.read) if self.c.physics else set()
            if first not in known:
                self.error(path, f"$physics.{first}: no such physics variable or param", self._suggest(first, known))
        elif root == "metrics":
            if first not in self.c.metrics:
                self.error(path, f"$metrics.{first}: no such metric", self._suggest(first, self.c.metrics))
        elif root == "series":
            if first not in self.c.metrics:
                self.error(path, f"$series.{first}: no such metric", self._suggest(first, self.c.metrics))
        elif root == "clock":
            if first not in ("round", "rounds", "left", "unit", "date", "start", "label", "time", "horizon"):
                self.error(path, f"$clock.{first}: no such field",
                           "clock fields: round, rounds, left, unit, date, start, label, time, horizon")

    def _spec_for(self, chain: Tuple[str, ...], types: Types, params: Mapping[str, C.ParamSpec]) -> Optional[Tuple[Any, str]]:
        """``(allowed values, kind)`` of the field a chain reads, when statically known."""
        root = chain[0]
        if root in types and len(chain) == 2:
            for kind in types[root]:
                spec = self.c.props_of(kind).get(chain[1]) if kind in self.c.types else None
                if spec is not None:
                    return spec.values, prop_type(spec)
        if root == "world" and len(chain) == 2 and chain[1] in self.c.world:
            spec = self.c.world[chain[1]]
            return spec.values, prop_type(spec)
        if root == "params" and params and chain[1] in params:
            param = params[chain[1]]
            if len(chain) == 2:
                return (param.values if isinstance(param.values, list) else None), param.type
            if len(chain) == 3 and param.type == "entity" and param.of in self.c.types:
                spec = self.c.props_of(param.of).get(chain[2])
                if spec is not None:
                    return spec.values, prop_type(spec)
        if root == "inputs" and len(chain) == 2 and chain[1] in self.c.inputs:
            spec_in = self.c.inputs[chain[1]]
            return spec_in.values, spec_in.type
        return None

    def _compare(self, known: Optional[Tuple[Any, str]], chain: Tuple[str, ...], word: str, path: str, source: str) -> None:
        field = "$" + ".".join(chain)
        if known is None:
            if word in _NULL_WORDS:
                self.warn(path, f"'{word}' is the text '{word}', not an empty value", f"write null for no value — in `{source}`")
            return
        values, kind = known
        if values:
            allowed = [str(v) for v in values]
            if word not in allowed:
                hint = get_close_matches(word, allowed, n=1)
                self.error(path, f"{field} is one of {', '.join(allowed)}; '{word}' is not",
                           (f"did you mean '{hint[0]}'?" if hint else "compare with one of the values") + f" — in `{source}`")
        elif kind in ("number", "int", "bool"):
            self.error(path, f"{field} is a {kind}, compared with the text '{word}'", f"fix the comparison — in `{source}`")
        elif word in _NULL_WORDS:
            self.warn(path, f"'{word}' is the text '{word}', not an empty value", f"write null for no value — in `{source}`")

    def _prop(self, type_names: Set[str], field: str, path: str, source: str, root: str) -> None:
        if field in ENTITY_FIELDS or field.isdigit():
            return
        known = [t for t in type_names if t in self.type_props]
        if not known:
            return
        if not any(field in self.type_props[t] for t in known):
            props = sorted(set().union(*(self.type_props[t] for t in known)))
            self.error(path, f"${root}.{field}: {'/'.join(sorted(known))} has no property '{field}'",
                       self._suggest(field, props) or f"properties: {', '.join(props) or 'none'} — in `{source}`")

    # -- sections -----------------------------------------------------------------------

    def run(self) -> None:
        c = self.c
        if c.fg_env != C.CONTRACT_VERSION:
            self.error("fg_env", f"unsupported contract version '{c.fg_env}'", f"use \"{C.CONTRACT_VERSION}\"")
        if not self.agents:
            self.warn("types", "no agent type, so nothing takes turns", "fine for a pure simulation; otherwise set agent: true")
        self._inputs()
        self._brief()
        self._clock_space()
        self._types_and_world()
        check_hooks(self, BASE)
        self._keyword_names()
        self._entities()
        check_inventory(self)
        self._relations()
        check_relation_fields(self, BASE)
        self._physics()
        check_physics_state(self, BASE)
        self._records()
        check_feeds(self, BASE)
        check_patterns(self, BASE)
        self._actions()
        self._stages()
        self._views()
        self._events()
        self._triggers()
        self._policies()
        self._measure()
        self._arms()
        self._calibration()
        self._defs_and_blocks()
        check_game(self)
        check_scans(self)
        check_assets(self, BASE)
