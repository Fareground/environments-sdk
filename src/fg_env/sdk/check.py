"""Static contract checking: every problem found at once, each with its path and a fix.

Beyond structure, the checker compiles every expression and template, confirms that
referenced types, properties, params, records, relations, stages, views, metrics and
inputs exist, and that each expression only uses roots available where it is written.
"""
from __future__ import annotations

import datetime as _dt
import re
from difflib import get_close_matches
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ValidationError

from ..physics import PhysicsExprError, _CompiledExpr, _CONSTS, _FUNCS
from . import contract as C
from .contract import Contract
from .effects import EFFECT_OPS, REPEAT_CEILING, RESERVED_ROOTS, statement_parts
from .errors import ContractError, Issue
from .expr import FUNCTIONS, ExprError, compile_expr, is_expr
from .inputs import check_value
from .template import compile_template
from .world import prop_type

__all__ = ["parse_contract", "check_contract"]

BASE = frozenset({"inputs", "world", "physics", "clock", "round", "stage", "metrics", "series", "arm", "pending"})
ENTITY_FIELDS = frozenset({"id", "name", "type", "alive", "at"})
ENTRY_FIELDS = frozenset({"seq", "round", "stage", "author", "to"})
RECORD_FIELD_TYPES = ("text", "number", "int", "bool", "list", "map", "any")
_COLLECTION_FUNCS = frozenset({"count", "sum", "avg", "min", "max", "top", "bottom", "filter", "map", "pick",
                               "any", "all", "ids", "first", "last", "shuffle", "sample", "choice"})

Types = Dict[str, Set[str]]

#: Bare words an author may mean as "no value"; in expressions they are plain text.
_NULL_WORDS = frozenset({"none", "None", "nil", "undefined", "Null", "NULL", "empty"})


def _all_field_names() -> List[str]:
    names: Set[str] = set()
    for obj in vars(C).values():
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            for name, info in obj.model_fields.items():
                names.add(info.alias or name)
    return sorted(names)


_FIELD_NAMES = _all_field_names()


def _path(loc: Sequence[Any]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part in ("function-after", "function-before", "function-wrap") or str(part).startswith("function-"):
            continue
        else:
            out += ("." if out else "") + str(part)
    return out or "(contract)"


def parse_contract(data: Any) -> Contract:
    """Validate structure. Raises :class:`ContractError` with every structural problem."""
    if isinstance(data, Contract):
        return data
    if not isinstance(data, Mapping):
        raise ContractError([Issue("(contract)", f"a contract is a JSON object, got {type(data).__name__}")])
    try:
        return Contract.model_validate(dict(data))
    except ValidationError as exc:
        issues = []
        for error in exc.errors():
            loc = [p for p in error["loc"] if not (isinstance(p, str) and ("[" in p or p.startswith("function")))]
            path = _path(loc)
            kind = error["type"]
            fix = None
            if kind == "extra_forbidden":
                key = str(loc[-1]) if loc else ""
                hint = get_close_matches(key, _FIELD_NAMES, n=1, cutoff=0.7)
                message = f"'{key}' is not a field here"
                if hint and hint[0] == key:
                    fix = f"'{key}' belongs to another part of the contract; remove it here"
                else:
                    fix = f"did you mean '{hint[0]}'?" if hint else "remove it"
            elif kind == "missing":
                message = "is required"
            else:
                message = error["msg"]
                fix = (error.get("ctx") or {}).get("fix")
            issues.append(Issue(path, message, fix))
        raise ContractError(_dedupe(issues)) from None


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


class _Checker:
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

    # -- reporting -----------------------------------------------------------------

    def error(self, path: str, message: str, fix: Optional[str] = None) -> None:
        self.issues.append(Issue(path, message, fix))

    def warn(self, path: str, message: str, fix: Optional[str] = None) -> None:
        self.issues.append(Issue(path, message, fix, "warning"))

    def _suggest(self, name: str, options: Iterable[str]) -> Optional[str]:
        hint = get_close_matches(name, list(options), n=1)
        return f"did you mean '{hint[0]}'?" if hint else None

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
        for expr in compiled.expressions:
            self._refs(expr, path, set(roots), types or {}, params or {})

    def _refs(self, compiled: Any, path: str, roots: Set[str], types: Types,
              params: Mapping[str, C.ParamSpec]) -> None:
        for root in compiled.roots:
            if root not in roots and not (root in self.c.defs and not self.c.defs[root].args):
                available = ", ".join(f"${r}" for r in sorted(roots))
                self.error(path, f"${root} is not available here", f"available: {available} — in `{compiled.source}`")
        for name, symbol in compiled.calls:
            if name in _COLLECTION_FUNCS and symbol is not None and symbol not in self.c.types:
                if symbol in self.c.records or name in ("choice", "min", "max"):
                    continue
                self.error(path, f"${name}({symbol}, …): '{symbol}' is not a declared type",
                           self._suggest(symbol, self.c.types) or f"types: {', '.join(self.c.types)}")
            if name == "records" and symbol is not None and symbol not in self.c.records:
                self.error(path, f"$records({symbol}): '{symbol}' is not a declared record",
                           self._suggest(symbol, self.c.records))
        for name in compiled.functions:
            if name not in FUNCTIONS and name not in self.c.defs:
                hint = get_close_matches(name, list(FUNCTIONS) + list(self.c.defs), n=1)
                self.error(path, f"unknown function ${name}",
                           (f"did you mean ${hint[0]}?" if hint else "declare it under `defs`") + f" — in `{compiled.source}`")
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
            if first not in ("round", "rounds", "left", "unit", "date", "label"):
                self.error(path, f"$clock.{first}: no such field", "clock fields: round, rounds, left, unit, date, label")

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

    # -- effects ---------------------------------------------------------------------

    def effects(self, effects: Any, path: str, roots: Set[str], types: Types,
                params: Optional[Mapping[str, C.ParamSpec]] = None) -> Set[str]:
        """Check an effect list; returns the roots available after it (locals included)."""
        roots = set(roots)
        if not isinstance(effects, list):
            self.error(path, "effects must be a list")
            return roots
        for index, effect in enumerate(effects):
            where = f"{path}[{index}]"
            if isinstance(effect, str):
                self._statement(effect, where, roots, types, params)
            elif isinstance(effect, dict):
                self._keyed(effect, where, roots, types, params)
            else:
                self.error(where, "an effect is an assignment text or an operation object")
        return roots

    def _statement(self, source: str, path: str, roots: Set[str], types: Types,
                   params: Optional[Mapping[str, C.ParamSpec]]) -> None:
        try:
            target, prop, local, _, right, index = statement_parts(source)
        except ExprError as exc:
            self.error(path, exc.detail, "write `$actor.cash -= 5`, `$world.open = true` or `$total = 3`")
            return
        self.expr(right, path, roots, types, params)
        if index is not None:
            self.expr(index, path, roots, types, params)
        if local is not None:
            if local in RESERVED_ROOTS:
                self.error(path, f"${local} cannot be reassigned", "assign to one of its fields")
            roots.add(local)
            return
        assert target is not None and prop is not None
        simple = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z_][A-Za-z0-9_]*)*)", target)
        if simple is None:
            self.expr(target, path, roots, types, params)
            return
        root = simple.group(1)
        fields = tuple(f for f in simple.group(2).split(".") if f) + (prop,)
        if root not in roots:
            self.error(path, f"${root} is not available here", f"available: {', '.join('$' + r for r in sorted(roots))}")
            return
        if root in ("inputs", "metrics", "series", "clock", "round", "stage", "arm"):
            self.error(path, f"${root} is read-only", "assign to an entity's property, $world.x or $physics.x")
            return
        self._chain((root, *fields), path, types, params or {}, source)

    def _keyed(self, effect: Dict[str, Any], path: str, roots: Set[str], types: Types,
               params: Optional[Mapping[str, C.ParamSpec]]) -> None:
        ops = [key for key in EFFECT_OPS if key in effect]
        if len(ops) != 1:
            keys = ", ".join(effect) or "none"
            hint = self._suggest(next(iter(effect), ""), EFFECT_OPS)
            self.error(path, f"an operation object names exactly one of: {', '.join(EFFECT_OPS)} (got keys {keys})", hint)
            return
        op = ops[0]
        allowed = set(EFFECT_OPS[op])
        if op != "post":
            for key in effect:
                if key not in allowed:
                    self.error(f"{path}.{key}", f"'{key}' is not part of `{op}`",
                               self._suggest(key, allowed) or f"`{op}` takes: {', '.join(sorted(allowed))}")
        v = lambda key, r=roots: self.value(effect.get(key), f"{path}.{key}", r, types, params)
        if op == "if":
            self.expr(effect["if"], f"{path}.if", roots, types, params)
            roots |= self.effects(effect.get("then", []), f"{path}.then", roots, types, params)
            roots |= self.effects(effect.get("else", []), f"{path}.else", roots, types, params)
        elif op == "each":
            v("each")
            name = effect.get("as") or "it"
            inner = roots | {name, "i"}
            inner_types = dict(types)
            source = effect["each"]
            if isinstance(source, str) and not is_expr(source):
                if self._type(source, f"{path}.each"):
                    inner_types[name] = {source}
            self.expr(effect.get("where"), f"{path}.where", inner, inner_types, params)
            self.effects(effect.get("do", []), f"{path}.do", inner, inner_types, params)
        elif op == "create":
            type_name = effect["create"]
            if self._type(type_name, f"{path}.create"):
                for prop, raw in (effect.get("props") or {}).items():
                    if prop not in self.type_props[type_name]:
                        self.error(f"{path}.props.{prop}", f"'{type_name}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[type_name]))
                    self.value(raw, f"{path}.props.{prop}", roots | {"i"}, types, params)
            v("count")
            count = effect.get("count")
            if isinstance(count, int) and not isinstance(count, bool) and count > C.MAX_CREATE:
                self.error(f"{path}.count", f"is {count:,}, above the ceiling of {C.MAX_CREATE:,}",
                           "create fewer entities at once")
            v("at")
            for key in ("id", "name"):
                self.template(effect.get(key), f"{path}.{key}", None, roots | {"i"}, types, params)
            if effect.get("as"):
                roots.add(effect["as"])
                types[effect["as"]] = {type_name}
        elif op in ("remove", "move", "wake"):
            v(op)
            if op == "move":
                v("to")
            if op == "wake":
                self.template(effect.get("why"), f"{path}.why", None, roots, types, params)
        elif op == "transfer":
            prop = effect["transfer"]
            if not any(prop in props for props in self.type_props.values()):
                self.error(f"{path}.transfer", f"no type has a property '{prop}'")
            into = effect.get("into")
            if into is not None and not any(into in props for props in self.type_props.values()):
                self.error(f"{path}.into", f"no type has a property '{into}'")
            for key in ("from", "to", "amount"):
                if key not in effect:
                    self.error(path, f"`transfer` needs `{key}`")
                v(key)
        elif op in ("link", "unlink"):
            if effect[op] not in self.c.relations:
                self.error(f"{path}.{op}", f"'{effect[op]}' is not a declared relation",
                           self._suggest(effect[op], self.c.relations) or "declare it under `relations`")
            for key in ("from", "to"):
                if key not in effect:
                    self.error(path, f"`{op}` needs `{key}`")
                v(key)
            if op == "link":
                v("value")
        elif op == "post":
            record = effect["post"]
            spec = self.c.records.get(record)
            if spec is None:
                self.error(f"{path}.post", f"'{record}' is not a declared record",
                           self._suggest(record, self.c.records) or "declare it under `records`")
            else:
                for key in effect:
                    if key not in ("post", "to", "author") and key not in spec.fields:
                        self.error(f"{path}.{key}", f"record '{record}' has no field '{key}'",
                                   self._suggest(key, spec.fields) or f"fields: {', '.join(spec.fields)}")
            for key, raw in effect.items():
                if key != "post":
                    self.value(raw, f"{path}.{key}", roots, types, params)
        elif op == "emit":
            self.template(effect.get("say"), f"{path}.say", None, roots, types, params)
            v("to")
            v("data")
        elif op == "fail":
            self.template(effect["fail"], f"{path}.fail", None, roots, types, params)
        elif op == "end":
            v("winner")
            self.template(effect.get("say"), f"{path}.say", None, roots, types, params)
        elif op == "after":
            v("after")
            self.effects(effect.get("do", []), f"{path}.do", roots, types, params)
        elif op == "block":
            block = self.c.blocks.get(effect["block"])
            given = effect.get("with") or {}
            if block is None:
                self.error(f"{path}.block", f"'{effect['block']}' is not a declared block",
                           self._suggest(effect["block"], self.c.blocks) or "declare it under `blocks`")
            elif not isinstance(given, dict):
                self.error(f"{path}.with", "`with` is an object of arguments")
            else:
                for name in sorted(set(block.args) - set(given)):
                    self.error(f"{path}.with", f"missing argument '{name}' for block '{effect['block']}'")
                for name in sorted(set(given) - set(block.args)):
                    self.error(f"{path}.with.{name}", f"block '{effect['block']}' has no argument '{name}'",
                               f"arguments: {', '.join(block.args) or 'none'}")
                for name, raw in given.items():
                    self.value(raw, f"{path}.with.{name}", roots, types, params)
        elif op == "repeat":
            v("repeat")
            limit = effect.get("repeat")
            if isinstance(limit, int) and not isinstance(limit, bool) and not 1 <= limit <= REPEAT_CEILING:
                self.error(f"{path}.repeat", f"is {limit:,}; a repeat limit runs from 1 to {REPEAT_CEILING:,}",
                           "use a smaller limit; a loop that needs more never settles")
            self.expr(effect.get("while"), f"{path}.while", roots, types, params)
            roots |= self.effects(effect.get("do", []), f"{path}.do", roots, types, params)

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
        self._entities()
        self._relations()
        self._physics()
        self._records()
        self._actions()
        self._stages()
        self._views()
        self._events()
        self._policies()
        self._measure()
        self._arms()
        self._defs_and_blocks()

    def _inputs(self) -> None:
        for name, spec in self.c.inputs.items():
            path = f"inputs.{name}"
            if spec.type not in C.INPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{spec.type}'", self._suggest(spec.type, C.INPUT_TYPES))
                continue
            if spec.type == "enum" and not spec.values:
                self.error(path, "an enum input needs `values`")
            if spec.type == "table":
                for column, kind in (spec.columns or {}).items():
                    if kind not in C.INPUT_TYPES or kind in ("table",):
                        self.error(f"{path}.columns.{column}", f"unknown column type '{kind}'")
            if spec.default is not None:
                problem = check_value(spec.type, spec.default, spec)
                if problem:
                    self.error(f"{path}.default", problem)
            elif not spec.required:
                self.warn(path, "has no default and is not required, so it may be null",
                          "give a default or set required: true")

    def _brief(self) -> None:
        roots, types = BASE | {"actor"}, {"actor": set(self.agents)}
        self.template(self.c.brief.situation or None, "brief.situation", "actor", roots, types)
        self.template(self.c.brief.rules or None, "brief.rules", "actor", roots, types)
        for type_name, text in self.c.brief.roles.items():
            if self._type(type_name, f"brief.roles.{type_name}", agent=True):
                self.template(text, f"brief.roles.{type_name}", "actor", roots, {"actor": {type_name}})

    def _clock_space(self) -> None:
        clock = self.c.clock
        if isinstance(clock.rounds, str):
            self.expr(clock.rounds, "clock.rounds", {"inputs"})
        elif clock.rounds < 1:
            self.error("clock.rounds", "must be at least 1")
        if clock.start:
            try:
                _dt.date.fromisoformat(clock.start[:10])
            except ValueError:
                self.error("clock.start", f"'{clock.start}' is not an ISO date", "e.g. 2026-01-31")
        if clock.step < 1:
            self.error("clock.step", "must be at least 1")
        if clock.start and clock.unit.lower().rstrip("s") not in ("day", "week", "month", "year", "hour", "minute"):
            self.warn("clock.start", f"a calendar date is not shown for unit '{clock.unit}'",
                      "use day, week, month, year, hour or minute")
        space = self.c.space
        if space is not None and sum(x is not None for x in (space.grid, space.graph, space.plane)) != 1:
            self.error("space", "declare exactly one of grid, graph, plane")

    def _prop_spec(self, spec: C.PropSpec, path: str, roots: Iterable[str]) -> None:
        if spec.type is not None and spec.type not in C.PROP_TYPES:
            self.error(f"{path}.type", f"unknown type '{spec.type}'", self._suggest(spec.type, C.PROP_TYPES))
        if spec.type == "enum" and not spec.values:
            self.error(path, "an enum property needs `values`")
        self.value(spec.default, f"{path}.default", roots)

    def _types_and_world(self) -> None:
        for name, spec in self.c.types.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                self.error(f"types.{name}", "type names are letters, digits and underscores")
            for prop, prop_spec in spec.props.items():
                if prop in ENTITY_FIELDS:
                    self.error(f"types.{name}.props.{prop}", f"'{prop}' is a built-in entity field", "choose another name")
                self._prop_spec(prop_spec, f"types.{name}.props.{prop}", BASE - {"metrics", "series"} | {"row", "i"})
            if spec.extends is not None:
                if spec.extends not in self.c.types:
                    self.error(f"types.{name}.extends", f"'{spec.extends}' is not a declared type",
                               self._suggest(spec.extends, self.c.types))
                elif name in self.c.lineage(spec.extends):
                    self.error(f"types.{name}.extends", "types extend each other in a cycle")
            if isinstance(spec.inspect, str):
                self.expr(spec.inspect, f"types.{name}.inspect", BASE | {"viewer", "it"},
                          {"viewer": set(self.agents), "it": set(self.c.subtypes(name))})
            if spec.policy is not None and spec.policy not in self.c.policies:
                self.error(f"types.{name}.policy", f"'{spec.policy}' is not a declared policy", self._suggest(spec.policy, self.c.policies))
            if self.c.is_agent(name) and not any(
                any(self.c.is_a(name, b) for b in ([a.by] if isinstance(a.by, str) else a.by)) for a in self.c.actions.values()
            ) and not any(self.c.is_a(other, name) and other != name for other in self.c.types):
                self.warn(f"types.{name}", "agent type has no actions", "add an action with `by`")
        for prop, world_spec in self.c.world.items():
            self._prop_spec(world_spec, f"world.{prop}", {"inputs"})

    def _entities(self) -> None:
        for eid, spec in self.c.entities.items():
            path = f"entities.{eid}"
            if self._type(spec.type, f"{path}.type"):
                for prop, raw in spec.props.items():
                    if prop not in self.type_props[spec.type]:
                        self.error(f"{path}.props.{prop}", f"'{spec.type}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[spec.type]))
                    self.value(raw, f"{path}.props.{prop}", BASE)
            if spec.type in self.c.types:
                self.template(spec.brief, f"{path}.brief", "actor", BASE | {"actor"}, {"actor": {spec.type}})
        for index, group in enumerate(self.c.population):
            path = f"population[{index}]"
            if not self._type(group.type, f"{path}.type"):
                continue
            if group.count is None and group.from_ is None:
                self.error(path, "give `count`, `from`, or both")
            self.value(group.count, f"{path}.count", BASE)
            self.expr(group.from_, f"{path}.from", BASE)
            self.expr(group.where, f"{path}.where", BASE | {"row"})
            self.expr(group.weight, f"{path}.weight", BASE | {"row"})
            for key in ("id", "name"):
                self.template(getattr(group, key), f"{path}.{key}", None, BASE | {"row", "i"})
            self.template(group.brief, f"{path}.brief", "actor", BASE | {"row", "i", "actor"}, {"actor": {group.type}})
            for prop, raw in group.props.items():
                if prop not in self.type_props[group.type]:
                    self.error(f"{path}.props.{prop}", f"'{group.type}' has no property '{prop}'",
                               self._suggest(prop, self.type_props[group.type]))
                self.value(raw, f"{path}.props.{prop}", BASE | {"row", "i"})

    def _relations(self) -> None:
        for index, link in enumerate(self.c.links):
            path = f"links[{index}]"
            if link.relation not in self.c.relations:
                self.error(f"{path}.relation", f"'{link.relation}' is not a declared relation",
                           self._suggest(link.relation, self.c.relations) or "declare it under `relations`")
            if link.among is not None:
                if self._type(link.among, f"{path}.among") and link.graph not in (None, "complete", "ring", "random", "small_world"):
                    self.error(f"{path}.graph", f"unknown graph '{link.graph}'", "complete, ring, random, small_world")
                self.expr(link.where, f"{path}.where", BASE | {"it"}, {"it": {link.among}})
                self.value(link.degree, f"{path}.degree", BASE)
                self.value(link.p, f"{path}.p", BASE | {"from", "to"}, {"from": {link.among}, "to": {link.among}})
            elif link.from_ is None or link.to is None:
                self.error(path, "give `from` and `to`, or `among` with a `graph`")
            else:
                for key, raw in (("from", link.from_), ("to", link.to)):
                    if not is_expr(raw) and raw not in self.c.entities:
                        self.warn(f"{path}.{key}", f"'{raw}' is not a named entity", "use an id from `entities` or an expression")

    def _physics(self) -> None:
        spec = self.c.physics
        if spec is None:
            return
        names = set(spec.vars) | set(spec.params) | set(spec.read) | set(_CONSTS) | set(_FUNCS) | {"t"}
        for name in spec.read:
            if name in spec.vars or name in spec.params:
                self.error(f"physics.read.{name}", f"'{name}' is also a variable or param, so the read would be ignored",
                           "give the read its own name and use it in the rates")
        for name, raw in spec.params.items():
            self.value(raw, f"physics.params.{name}", {"inputs", "world"})
        for name, src in spec.read.items():
            self.expr(src, f"physics.read.{name}", BASE - {"physics", "metrics", "series"})
        for name, var in spec.vars.items():
            self.value(var.start, f"physics.vars.{name}.start", {"inputs", "world"})
            if var.rate is not None:
                self._physics_expr(var.rate, f"physics.vars.{name}.rate", names)
        for target, src in spec.write.items():
            path = f"physics.write.{target}"
            owner, _, prop = target.partition(".")
            if owner == "world":
                if prop not in self.c.world:
                    self.error(path, f"world has no property '{prop}'", self._suggest(prop, self.c.world))
            elif owner in self.c.types:
                if prop not in self.type_props[owner]:
                    self.error(path, f"'{owner}' has no property '{prop}'", self._suggest(prop, self.type_props[owner]))
            else:
                self.error(path, "write targets are 'world.<prop>' or '<type>.<prop>'")
            self._physics_expr(src, path, names)

    def _physics_expr(self, source: str, path: str, names: Set[str]) -> None:
        try:
            compiled = _CompiledExpr(source)
        except PhysicsExprError as exc:
            self.error(path, str(exc), "physics math uses bare names: beta*S*I/N")
            return
        unknown = compiled._names - names
        if unknown:
            self.error(path, f"unknown name(s) {sorted(unknown)}", "use physics variables, params or read names")

    def _records(self) -> None:
        for name, spec in self.c.records.items():
            path = f"records.{name}"
            for field, kind in spec.fields.items():
                if kind not in RECORD_FIELD_TYPES:
                    self.error(f"{path}.fields.{field}", f"unknown field type '{kind}'", ", ".join(RECORD_FIELD_TYPES))
                if field in ENTRY_FIELDS:
                    self.error(f"{path}.fields.{field}", f"'{field}' is a built-in entry field")
            if spec.visible != "all":
                self.expr(spec.visible, f"{path}.visible", BASE | {"viewer", "it"}, {"viewer": set(self.agents)})
            self.template(spec.show, f"{path}.show", "it", BASE | {"actor", "it"}, {"actor": set(self.agents)})

    def _actions(self) -> None:
        for name, spec in self.c.actions.items():
            path = f"actions.{name}"
            by = [spec.by] if isinstance(spec.by, str) else spec.by
            by_types = {t for t in by if self._type(t, f"{path}.by", agent=True)}
            types: Types = {"actor": by_types}
            for pname, param in spec.params.items():
                ppath = f"{path}.params.{pname}"
                if param.type not in C.PARAM_TYPES:
                    self.error(f"{ppath}.type", f"unknown type '{param.type}'", self._suggest(param.type, C.PARAM_TYPES))
                    continue
                if param.type == "entity":
                    if param.of is None:
                        self.error(ppath, "an entity parameter needs `of` (the entity type)")
                    elif self._type(param.of, f"{ppath}.of"):
                        self.expr(param.where, f"{ppath}.where", BASE | {"actor", "it", "i", "params"},
                                  {"actor": by_types, "it": {param.of}}, spec.params)
                elif param.type == "enum":
                    if param.values is None:
                        self.error(ppath, "an enum parameter needs `values`")
                    self.value(param.values, f"{ppath}.values", BASE | {"actor", "params"}, types, spec.params)
                for key in ("min", "max", "default"):
                    self.value(getattr(param, key), f"{ppath}.{key}", BASE | {"actor", "params"}, types, spec.params)
                if param.type not in ("number", "int") and (param.min is not None or param.max is not None):
                    self.error(ppath, "min/max apply to number and int parameters")
            for index, condition in enumerate(spec.when):
                self.expr(condition.expr, f"{path}.when[{index}]", BASE | {"actor"}, types)
            roots = set(BASE | {"actor", "params"})
            self.value(spec.chance, f"{path}.chance", roots, types, spec.params)
            after = self.effects(spec.do, f"{path}.do", roots, dict(types), spec.params)
            after |= self.effects(spec.otherwise, f"{path}.otherwise", roots, dict(types), spec.params)
            if spec.otherwise and spec.chance is None:
                self.warn(f"{path}.otherwise", "runs only when `chance` fails, and there is no `chance`")
            for key in ("outcome", "announce"):
                self.template(getattr(spec, key), f"{path}.{key}", None, after, types, spec.params)
            if isinstance(spec.terminal, str):
                self.expr(spec.terminal, f"{path}.terminal", after, types, spec.params)
            for pname, param in spec.params.items():
                self.template(param.invalid, f"{path}.params.{pname}.invalid", None,
                              BASE | {"actor", "params", "value"}, types, spec.params)
            if not any(name in _stage_action_names(s, self.c) for s in self.c.stage_list()):
                self.warn(path, "is not available in any stage", "add it to a stage's `actions`")

    def _stages(self) -> None:
        seen: Set[str] = set()
        for index, stage in enumerate(self.c.stages):
            path = f"stages[{index}]"
            if stage.name in seen:
                self.error(f"{path}.name", f"duplicate stage name '{stage.name}'")
            seen.add(stage.name)
            names = _stage_action_names(stage, self.c, raw=True)
            for action in names:
                if action not in self.c.actions:
                    self.error(f"{path}.actions", f"'{action}' is not a declared action",
                               self._suggest(action, self.c.actions))
            if isinstance(stage.actions, dict):
                for type_name in stage.actions:
                    self._type(type_name, f"{path}.actions.{type_name}", agent=True)
            elif isinstance(stage.actions, str) and stage.actions != "all":
                self.error(f"{path}.actions", "use 'all', a list of action names, or {type: [actions]}")
            if stage.turns not in ("sequential", "simultaneous"):
                self.error(f"{path}.turns", f"unknown turns '{stage.turns}'", "sequential or simultaneous")
            if stage.quiet not in ("wake", "skip"):
                self.error(f"{path}.quiet", f"unknown quiet '{stage.quiet}'", "wake or skip")
            if stage.max_actions < 1 or stage.max_calls < 1:
                self.error(path, "max_actions and max_calls must be at least 1")
            agent_types: Types = {"it": set(self.agents)}
            if stage.order not in ("seat", "random"):
                self.expr(stage.order, f"{path}.order", BASE | {"it", "i"}, agent_types)
            self.expr(stage.who, f"{path}.who", BASE | {"it", "i"}, agent_types)
            self.expr(stage.until, f"{path}.until", BASE)
            self.expr(stage.when, f"{path}.when", BASE)
            self.template(stage.brief or None, f"{path}.brief", "actor", BASE | {"actor"}, {"actor": set(self.agents)})
            self.effects(stage.on_enter, f"{path}.on_enter", set(BASE), {})
            self.effects(stage.on_exit, f"{path}.on_exit", set(BASE), {})
            self.effects(stage.on_idle, f"{path}.on_idle", set(BASE) | {"actor"}, {"actor": set(self.agents)})

    def _views(self) -> None:
        for name, view in self.c.views.items():
            path = f"views.{name}"
            targets = [view.for_] if isinstance(view.for_, str) else view.for_
            actor_types = set(self.agents) if targets == ["all"] else {t for t in targets if self._type(t, f"{path}.for", agent=True)}
            types: Types = {"actor": actor_types}
            for stage in view.stages or []:
                if stage not in self.stage_names:
                    self.error(f"{path}.stages", f"'{stage}' is not a stage", self._suggest(stage, self.stage_names))
            self.expr(view.when, f"{path}.when", BASE | {"actor"}, types)
            if view.of is None:
                self.template(view.show, f"{path}.show", "actor", BASE | {"actor"}, types)
                continue
            item_roots = BASE | {"actor", "it", "i"}
            if view.of in self.c.types:
                types["it"] = {view.of}
            elif view.of in self.c.records:
                pass
            else:
                self.expr(view.of, f"{path}.of", BASE | {"actor"}, types)
            self.expr(view.where, f"{path}.where", item_roots, types)
            self.expr(view.sort, f"{path}.sort", item_roots, types)
            self.template(view.show, f"{path}.show", "it", item_roots, types)
            if view.limit is not None and view.limit < 1:
                self.error(f"{path}.limit", "must be at least 1")

    def _events(self) -> None:
        for index, event in enumerate(self.c.events):
            path = f"events[{index}]"
            if event.phase not in ("start", "end"):
                self.error(f"{path}.phase", f"unknown phase '{event.phase}'", "start or end")
            for arm in event.arms or []:
                if arm not in self.c.arms:
                    self.error(f"{path}.arms", f"'{arm}' is not a declared arm", self._suggest(arm, self.c.arms))
            self.value(event.at, f"{path}.at", BASE)
            self.expr(event.when, f"{path}.when", BASE)
            self.value(event.chance, f"{path}.chance", BASE)
            types: Types = {}
            roots = set(BASE)
            if event.each is not None:
                item = event.as_ or "it"
                roots |= {item, "i"}
                if event.each in self.c.types:
                    types[item] = {event.each}
                else:
                    self.expr(event.each, f"{path}.each", BASE)
            self.expr(event.where, f"{path}.where", roots, types)
            self.effects(event.do, f"{path}.do", roots, types)
            self.template(event.say, f"{path}.say", None, BASE)
            if not event.do and not event.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _policies(self) -> None:
        for name, policy in self.c.policies.items():
            for index, rule in enumerate(policy.rules):
                path = f"policies.{name}.rules[{index}]"
                action = self.c.actions.get(rule.do)
                if rule.do != "pass" and action is None:
                    self.error(f"{path}.do", f"'{rule.do}' is not a declared action", self._suggest(rule.do, self.c.actions))
                actor_types: Types = {"actor": set(self.agents)}
                if action is not None:
                    actor_types = {"actor": set([action.by] if isinstance(action.by, str) else action.by)}
                    for key in rule.with_:
                        if key not in action.params:
                            self.error(f"{path}.with.{key}", f"'{rule.do}' has no parameter '{key}'",
                                       self._suggest(key, action.params))
                rule_roots = BASE | {"actor"}
                if rule.each is not None:
                    if rule.each not in self.c.types:
                        self.expr(rule.each, f"{path}.each", BASE | {"actor"}, actor_types)
                    else:
                        actor_types = {**actor_types, "it": set(self.c.subtypes(rule.each))}
                    rule_roots = rule_roots | {"it", "i"}
                self.expr(rule.when, f"{path}.when", rule_roots, actor_types)
                self.value(rule.chance, f"{path}.chance", rule_roots, actor_types)
                self.value(rule.with_, f"{path}.with", rule_roots, actor_types)

    def _measure(self) -> None:
        for name, metric in self.c.metrics.items():
            self.expr(metric.expr, f"metrics.{name}", BASE)
        for name, output in self.c.outputs.items():
            path = f"outputs.{name}"
            if output.type not in C.OUTPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{output.type}'", self._suggest(output.type, C.OUTPUT_TYPES))
            self.expr(output.expr, path, BASE | {"outputs"})
        for index, end in enumerate(self.c.end):
            self.expr(end.when, f"end[{index}].when", BASE)
            self.expr(end.winner, f"end[{index}].winner", BASE)
            self.template(end.say, f"end[{index}].say", None, BASE)
        for index, invariant in enumerate(self.c.invariants):
            self.expr(invariant.expr, f"invariants[{index}]", BASE)
        if not self.c.outputs:
            self.warn("outputs", "no outputs declared", "declare the typed results this environment produces")

    def _defs_and_blocks(self) -> None:
        for name, spec in self.c.defs.items():
            path = f"defs.{name}"
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                self.error(path, "def names are letters, digits and underscores")
            if name in FUNCTIONS:
                self.error(path, f"'{name}' is a built-in function", "choose another name")
            for arg in spec.args:
                if arg in BASE or arg in RESERVED_ROOTS:
                    self.error(f"{path}.args", f"'{arg}' is a built-in root", "choose another argument name")
            self.expr(spec.expr, f"{path}.expr", BASE | set(spec.args))
            bare: Set[str] = set()
            try:
                bare = set(compile_expr(spec.expr).symbols) & set(spec.args)
            except ExprError:
                pass
            for arg in sorted(bare):
                self.error(f"{path}.expr", f"argument '{arg}' is written without $, so it is the text '{arg}'",
                           f"write ${arg}")
        for name, block in self.c.blocks.items():
            path = f"blocks.{name}"
            for arg in block.args:
                if arg in BASE or arg in RESERVED_ROOTS:
                    self.error(f"{path}.args", f"'{arg}' is a built-in root", "choose another argument name")
            self.effects(block.do, f"{path}.do", set(BASE) | set(block.args), {})

    def _arms(self) -> None:
        for name, arm in self.c.arms.items():
            for key in arm.inputs:
                if key not in self.c.inputs:
                    self.error(f"arms.{name}.inputs.{key}", f"'{key}' is not a declared input", self._suggest(key, self.c.inputs))
            for key in arm.patch:
                if key not in Contract.model_fields:
                    self.error(f"arms.{name}.patch.{key}", f"'{key}' is not a contract section",
                               self._suggest(key, Contract.model_fields))


def _stage_action_names(stage: C.StageSpec, contract: Contract, raw: bool = False) -> List[str]:
    if stage.actions == "all":
        return [] if raw else list(contract.actions)
    if isinstance(stage.actions, list):
        return list(stage.actions)
    if isinstance(stage.actions, dict):
        return [a for names in stage.actions.values() for a in names]
    return []
