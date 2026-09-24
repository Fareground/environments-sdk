"""Static contract checking: every problem found at once, each with its path and a fix.

Beyond structure, the checker compiles every expression and template, confirms that
referenced types, properties, params, records, relations, stages, views, metrics and
inputs exist, and that each expression only uses roots available where it is written.

The checker's sections live beside it: effects (:mod:`.effects`), the world model
(:mod:`.world`), actions, stages and views (:mod:`.actions`), and events, policies,
measures, defs and arms (:mod:`.rules`).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from difflib import get_close_matches
from typing import Any

from pydantic import ValidationError

from .. import contract as C
from ..assets.checks import check_assets
from ..contract import Contract
from ..contract.base import TYPE_SYNONYMS
from ..contract.normalize import normalize
from ..contract.parse_errors import validation_issues
from ..errors import ContractError, Issue
from ..expr import FUNCTIONS, ExprError, Scope, compile_expr, is_expr
from ..expr.base import WrongKind
from ..expr.calls import callable_in, callable_names, suggest_function
from ..expr.codegen import _ITEM_ROOTS
from ..expr.template import compile_template, quoted_placeholders
from ..host.common import raw_model_ids
from ..patterns.check import check_pattern_call, check_patterns
from ..runtime.returns import check_game
from ..world.live import prop_type
from .actions import ActionChecks
from .effects import EffectChecks
from .inventory import check_inventory
from .privacy import PrivacyChecks
from .roots import BASE, ENTITY_FIELDS, Types
from .rules import RuleChecks
from .scans import check_scans
from .state import check_feeds, check_physics_state, check_relation_fields
from .world import WorldChecks

__all__ = ["parse_contract", "check_contract"]


#: Collection functions whose first parameter is not spelled ``items``.
_OTHER_COLLECTION_FUNCS = frozenset({"first", "last"})


def _collection_funcs() -> set[str]:
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
    from ..mechanisms import expand_mechanisms

    source = normalize(data)[0]
    expanded, mechanism_issues = expand_mechanisms(source)
    if mechanism_issues:
        raise ContractError(_dedupe(mechanism_issues))
    expanded = normalize(expanded)[0]  # what mechanisms and imports generated in an earlier form
    try:
        contract = Contract.model_validate(expanded)
        contract._source = source
        return contract
    except ValidationError as exc:
        raise ContractError(_dedupe(validation_issues(exc))) from None


def _dedupe(issues: Iterable[Issue]) -> list[Issue]:
    seen, out = set(), []
    for issue in issues:
        key = (issue.path, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def check_contract(contract: Contract) -> list[Issue]:
    """All semantic errors and warnings (errors first)."""
    checker = _Checker(contract)
    checker.run()
    issues = _dedupe(checker.issues)
    return [i for i in issues if i.severity == "error"] + [i for i in issues if i.severity != "error"]


class _Checker(EffectChecks, WorldChecks, ActionChecks, PrivacyChecks, RuleChecks):
    def __init__(self, contract: Contract):
        self.c = contract
        self.issues: list[Issue] = []
        self.type_props: dict[str, set[str]] = {t: set(contract.props_of(t)) for t in contract.types}
        self.agents = contract.agent_types()
        words: set[str] = set(contract.types) | set(contract.records) | set(contract.relations) | set(contract.actions)
        words |= ({s.name for s in contract.stage_list()} | set(contract.outputs)
                  | {name for spec in contract.types.values() for name in spec.policies}
                  | set(contract.arms))
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
        self.families = contract.mechanism_families()

    # -- reporting -----------------------------------------------------------------

    def error(self, path: str, message: str, fix: str | None = None) -> None:
        self.issues.append(Issue(path, message, fix))

    def warn(self, path: str, message: str, fix: str | None = None) -> None:
        self.issues.append(Issue(path, message, fix, "warning"))

    def _suggest(self, name: str, options: Iterable[str]) -> str | None:
        hint = get_close_matches(name, list(options), n=1)
        return f"did you mean '{hint[0]}'?" if hint else None

    def _suggest_type(self, name: str, options: Iterable[str]) -> str:
        """A did-you-mean over the type names ``options`` and their common spellings (`string` is text), naming the
        type as the contract writes it; else the list of types."""
        options = list(options)
        spellings = {**{option: option for option in options},
                     **{word: kind for word, kind in TYPE_SYNONYMS.items() if kind in options}}
        hint = get_close_matches(name, list(spellings), n=1)
        return f"did you mean '{spellings[hint[0]]}'?" if hint else f"types: {', '.join(options)}"

    def order_setting(self, order: str | None, path: str, words: tuple[str, ...], roots: Iterable[str],
                      types: Types | None = None) -> None:
        """An `order` setting: one of ``words``, or an expression (lowest first). A bare word is never an expression."""
        if order is None or order in words:
            return
        if not is_expr(order):
            self.error(path, f"unknown order '{order}'",
                       self._suggest(order, words) or f"use {' or '.join(words)}, or an expression like $it.priority")
            return
        self.expr(order, path, roots, types)

    def _hint(self, name: str, options: Iterable[str], what: str) -> str:
        """Did-you-mean when a name is close, otherwise the names there are to choose from."""
        listed = list(options)
        return self._suggest(name, listed) or (f"{what}: {', '.join(listed)}" if listed else f"no {what} declared")

    def _type(self, name: str | None, path: str, agent: bool = False) -> bool:
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

    def expr(self, source: Any, path: str, roots: Iterable[str], types: Types | None = None,
             params: Mapping[str, C.ParamSpec] | None = None) -> None:
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

    def condition(self, source: Any, path: str, roots: Iterable[str], types: Types | None = None,
                  params: Mapping[str, C.ParamSpec] | None = None, fix: str | None = None) -> None:
        """A condition: an expression, or true / false. Text there (a bare word like `deal`) is always true."""
        self.expr(source, path, roots, types, params)
        if not isinstance(source, str) or is_expr(source):
            return
        try:
            value = compile_expr(source)(Scope())
        except ExprError:
            return  # reported by expr
        if isinstance(value, str) and value:
            self.error(path, f"`{source}` is the text '{value}', which is always true: a condition is an expression",
                       fix or self._condition_fix(value, set(roots)))

    def _condition_fix(self, word: str, roots: set[str]) -> str:
        if word in self.c.world:
            return f"did you mean $world.{word}?"
        owners = [f"${root}.{word}" for root in ("actor", "it", "viewer") if root in roots]
        if owners and any(word in props for props in self.type_props.values()):
            return f"did you mean {' or '.join(owners)}?"
        return "write an expression like `$world.open` or `$round > 3`; true and false are the constants"

    def value(self, raw: Any, path: str, roots: Iterable[str], types: Types | None = None,
              params: Mapping[str, C.ParamSpec] | None = None) -> None:
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

    def template(self, source: str | None, path: str, subject: str | None, roots: Iterable[str],
                 types: Types | None = None, params: Mapping[str, C.ParamSpec] | None = None) -> None:
        if source is None:
            return
        try:
            compiled = compile_template(source, subject)
        except ExprError as exc:
            self.error(path, exc.detail, f"template: {source}")
            return
        for placeholder in quoted_placeholders(source):
            self.warn(path, f"wraps {placeholder} in «»: participant text already reads inside «», so this shows "
                            "««…»», and other text in «» reads as written by participants",
                      f"write {placeholder} without the «»")
        for expr in compiled.expressions:
            self._refs(expr, path, set(roots), types or {}, params or {})

    def _refs(self, compiled: Any, path: str, roots: set[str], types: Types,
              params: Mapping[str, C.ParamSpec]) -> None:
        # An unknown callee may be the collection function that binds $it, $i and $outer (a misspelled $max): report
        # the name to repair, not those roots. Independent errors are kept.
        exprs = self.c.expr_defs()
        for name in sorted(name for name in compiled.functions if name in self.c.defs and name not in exprs):
            self.error(path, f"${name}: def '{name}' runs effects, so it is not read as a value",
                       f"run it as an effect: {{\"call\": \"{name}\", \"with\": {{...}}}} — in `{compiled.source}`")
        unknown = sorted(name for name in compiled.functions
                         if not callable_in(name, self.families) and name not in self.c.defs)
        for name in unknown:
            if name in FUNCTIONS:
                families = FUNCTIONS[name].families
                self.error(path, f"${name} reads a {' or '.join(f'`{f}`' for f in families)} mechanism, and this "
                                 "contract declares none",
                           f"declare one (guide('{families[0]}')) — in `{compiled.source}`")
                continue
            hint = suggest_function(name, callable_names(self.families) + list(exprs))
            self.error(path, f"unknown function ${name}",
                       (f"did you mean {hint}?" if hint else "declare it under `defs`") + f" — in `{compiled.source}`")
        for root in compiled.roots:
            if unknown and root in _ITEM_ROOTS:
                continue
            if root not in roots and not (root in exprs and not exprs[root].args):
                available = ", ".join(f"${r}" for r in sorted(roots))
                self.error(path, f"${root} is not available here", f"available: {available} — in `{compiled.source}`")
        if not (compiled.roots or compiled.functions or compiled.methods):
            try:  # literals only: text where a number is needed fails the same way every time it runs
                compiled(Scope())
            except WrongKind as exc:
                self.error(path, f"{exc.detail} — in `{compiled.source}`",
                           "text is not a number: write the number without quotes, or join text with text "
                           "($text(3) turns a number into text)")
            except ExprError:
                pass  # other failures (1/0) are reported where the expression runs
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
            if name not in self.c.defs and callable_in(name, self.families):  # else it is reported as unavailable
                self.error(path, f"wrong number of arguments: ${signature}", f"in `{compiled.source}`")
        for chain, word in compiled.comparisons:
            self._compare(self._spec_for(chain, types, params), chain, word, path, compiled.source)
        for _, symbol, chain, word in compiled.item_comparisons:
            if symbol in self.c.types and len(chain) == 2:
                spec = self.c.props_of(symbol).get(chain[1])
                self._compare((spec.values, prop_type(spec)) if spec else None, chain, word, path, compiled.source)
        actor_props: set[str] = set()
        for kind in types.get("actor", ()):
            actor_props |= self.type_props.get(kind, set())
        for word in compiled.symbols:
            if word in actor_props and word not in self.known_words:
                self.warn(path, f"bare word '{word}' is the text '{word}'",
                          f"did you mean $actor.{word}? — in `{compiled.source}`")
        for chain in compiled.paths:
            self._chain(chain, path, types, params, compiled.source)
        for _, symbol, chain in compiled.item_paths:
            if symbol in self.c.types and len(chain) > 1:
                self._prop({symbol}, chain[1], path, compiled.source, "it")

    def _chain(self, chain: tuple[str, ...], path: str, types: Types, params: Mapping[str, C.ParamSpec],
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
            elif (of := params[first].of) is not None and params[first].type == "entity" and len(fields) > 1 \
                    and of in self.c.types:
                self._prop({of}, fields[1], path, source, f"params.{first}")
        elif root == "world":
            if first not in self.c.world:
                self.error(path, f"$world.{first}: no such world property",
                           self._suggest(first, self.c.world) or "declare it under `world`")
        elif root == "inputs":
            if first not in self.c.inputs:
                self.error(path, f"$inputs.{first}: no such input",
                           self._suggest(first, self.c.inputs) or "declare it under `inputs`")
        elif root == "physics":
            known = (set(self.c.physics.vars) | set(self.c.physics.params) | set(self.c.physics.read) if self.c.physics
                     else set())
            if first not in known:
                self.error(path, f"$physics.{first}: no such physics variable or param", self._suggest(first, known))
        elif root in ("outputs", "series"):
            self._output_read(root, first, path)
        elif root == "clock":
            if first not in ("round", "rounds", "left", "unit", "date", "start", "label"):
                self.error(path, f"$clock.{first}: no such field", "clock fields: round, rounds, left, unit, date, "
                                                                   "start, label")

    def _output_read(self, root: str, name: str, path: str) -> None:
        """``$outputs.name`` / ``$series.name``: during the run only series outputs have values; an output's own
        expression may also read the outputs worked out before it."""
        sampled = self.c.series_outputs()
        reader = self.c.outputs.get(path.split(".")[1]) if path.startswith("outputs.") else None
        at_end = root == "outputs" and reader is not None and reader.series is not True \
            and not path.endswith(".series")
        if name in sampled or (at_end and name in self.c.outputs):
            return
        if name in self.c.outputs:
            self.error(path, f"${root}.{name}: outputs.{name} is worked out only when the run ends",
                       f"add \"series\": true to outputs.{name} to sample it every round")
        else:
            self.error(path, f"${root}.{name}: no such output",
                       self._suggest(name, sampled if not at_end else self.c.outputs) or "declare it under `outputs` "
                       "with \"series\": true")

    def _spec_for(self, chain: tuple[str, ...], types: Types,
                  params: Mapping[str, C.ParamSpec]) -> tuple[Any, str] | None:
        """``(allowed values, kind)`` of the field a chain reads, when statically known."""
        root = chain[0]
        named = self.c.named_entities().get(root[len("entity("):-1]) if root.startswith("entity(") else None
        if named is not None and named.type in self.c.types and len(chain) == 2 \
                and chain[1] in self.c.props_of(named.type):
            spec = self.c.props_of(named.type)[chain[1]]
            return spec.values, prop_type(spec)
        if root in types and len(chain) == 2:
            specs = [self.c.props_of(kind).get(chain[1]) if kind in self.c.types else None
                     for kind in sorted(types[root])]
            if not specs or any(spec is None for spec in specs):
                return None
            known_specs = [spec for spec in specs if spec is not None]
            kinds = {prop_type(spec) for spec in known_specs}
            if len(kinds) != 1:
                return None
            values = None
            if all(spec.values for spec in known_specs):
                values = []
                for spec in known_specs:
                    for value in spec.values or []:
                        if value not in values:
                            values.append(value)
            return values, kinds.pop()
        if root == "world" and len(chain) == 2 and chain[1] in self.c.world:
            spec = self.c.world[chain[1]]
            return spec.values, prop_type(spec)
        if root == "params" and params and chain[1] in params:
            param = params[chain[1]]
            if len(chain) == 2:
                return (param.values if isinstance(param.values, list) else None), param.type
            if len(chain) == 3 and param.type == "entity" and param.of in self.c.types:
                field = self.c.props_of(param.of).get(chain[2])
                if field is not None:
                    return field.values, prop_type(field)
        if root == "inputs" and len(chain) == 2 and chain[1] in self.c.inputs:
            spec_in = self.c.inputs[chain[1]]
            return spec_in.values, spec_in.type
        return None

    def _compare(self, known: tuple[Any, str] | None, chain: tuple[str, ...], word: str, path: str,
                 source: str) -> None:
        field = "$" + ".".join(chain)
        if known is None:
            if word in _NULL_WORDS:
                self.warn(path, f"'{word}' is the text '{word}', not an empty value",
                          f"write null for no value — in `{source}`")
            return
        values, kind = known
        if values:
            allowed = [str(v) for v in values]
            if word not in allowed:
                hint = get_close_matches(word, allowed, n=1)
                self.error(path, f"{field} is one of {', '.join(allowed)}; '{word}' is not",
                           (f"did you mean '{hint[0]}'?" if hint else "compare with one of the values")
                           + f" — in `{source}`")
        elif kind in ("number", "int", "bool"):
            self.error(path, f"{field} is a {kind}, compared with the text '{word}'",
                       f"fix the comparison — in `{source}`")
        elif word in _NULL_WORDS:
            self.warn(path, f"'{word}' is the text '{word}', not an empty value",
                      f"write null for no value — in `{source}`")

    def _prop(self, type_names: set[str], field: str, path: str, source: str, root: str) -> None:
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
        self._inputs()
        self._brief()
        self._clock_space()
        self._types_and_world()
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
        self._secret_subtypes()
        self._events()
        self._policies()
        self._measure()
        self._arms()
        self._defs()
        check_game(self)
        check_scans(self)
        check_assets(self, BASE)
        from ..mechanisms import authored_slips, separate_turns

        self.issues.extend(separate_turns(c._source or {}))
        self.issues.extend(authored_slips(c._source or {}))
        self.issues.extend(raw_model_ids(c._source or {}))
