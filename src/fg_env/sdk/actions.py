"""Actions as tools: which are legal, their JSON Schemas, argument validation, atomic apply."""
from __future__ import annotations

import json

import math
import re
import reprlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..entity import Entity
from .assets.delivery import attached_ids
from .assets.intake import file_schema, file_value
from .contract import MAX_LIST_ITEMS, ActionSpec, Contract, ParamSpec, RecordSpec, StageSpec
from .effects import EffectRunner
from .errors import RunError
from .expr import EVAL_BUDGET, ExprError, Untrusted, compile_expr, is_expr, nested_free, resolve, shared_budget, truthy
from .template import compile_template, format_value
from .tool_text import shared_description, shared_param, text_limit, usage_limits
from .world import Abort, SdkWorld, _plain

__all__ = ["ACTION_BUDGET", "TEXT_MAX_LEN", "MAX_SAFE_INT", "ToolSpec", "Outcome", "ActionBook", "stage_actions"]

#: Work one action application may do in total (all its conditions, effects and templates).
ACTION_BUDGET = 5 * EVAL_BUDGET

#: Longest text a participant may pass to a text parameter that declares no `max_len`.
TEXT_MAX_LEN = 4_000
#: Largest magnitude a number argument may have (the whole numbers JSON carries exactly).
MAX_SAFE_INT = 2**53 - 1
#: Longest text read as a number (`"12.5"`); anything longer is not a number.
_NUMBER_TEXT = 64
#: Unknown argument names listed in one correction.
_LISTED_UNKNOWN = 8
#: Significant digits kept for schema bounds and defaults (0.1 + 0.2 shows as 0.3).
_SCHEMA_DIGITS = 12
_LEFTOVER_EXPR = re.compile(r"\$['\"(A-Za-z_]")
#: How far (in steps) a number may sit from a step boundary and still count as on it (float noise).
_STEP_TOLERANCE = 1e-9

_PREVIEW = reprlib.Repr()
_PREVIEW.maxstring = 60
_PREVIEW.maxother = 60
_PREVIEW.maxlevel = 3
_PREVIEW.maxlist = 6
_PREVIEW.maxdict = 6


def _preview(value: Any) -> str:
    """A short, safe rendering of an argument for a correction message, whatever it is."""
    return _PREVIEW.repr(value)


def _tidy(value: Any) -> Any:
    """Drop float noise: 0.30000000000000004 → 0.3."""
    return float(f"{value:.{_SCHEMA_DIGITS}g}") if isinstance(value, float) else value

#: Entity choices listed inline (id = name) in a tool schema up to this many.
_NAMED_CHOICES = 12
#: Entity choices listed as an id enum up to this many; beyond it the id is free text.
_ENUM_CHOICES = 60


@dataclass(frozen=True)
class ToolSpec:
    """One tool offered to an agent this turn."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    kind: str = "act"  # act | look | end
    terminal: bool = False

    def to_anthropic(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def to_openai(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.input_schema,
        }}

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "kind": self.kind, "terminal": self.terminal}


@dataclass
class Outcome:
    ok: bool
    text: str
    success: bool = True
    params: Dict[str, Any] = field(default_factory=dict)
    #: The assets the action's `attach` delivers to its actor.
    assets: List[str] = field(default_factory=list)


def stage_actions(contract: Contract, stage: StageSpec, type_name: str) -> List[str]:
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
    out = []
    for name in names:
        action = contract.actions.get(name)
        if action is None:
            continue
        by = [action.by] if isinstance(action.by, str) else action.by
        if any(contract.is_a(type_name, allowed) for allowed in by):
            out.append(name)
    return out


class ActionBook:
    def __init__(self, contract: Contract, world: SdkWorld, effects: EffectRunner):
        self.contract = contract
        self.world = world
        self.effects = effects
        #: Shared tool name → the actions offered inside it, in declaration order.
        self.groups: Dict[str, List[str]] = {}
        for name, spec in contract.actions.items():
            if spec.tool is not None:
                self.groups.setdefault(spec.tool, []).append(name)

    # -- legality -------------------------------------------------------------

    def blocked(self, actor: Entity, name: str, used_turn: Dict[str, int], used_round: Dict[str, int]) -> Optional[str]:
        """Why ``name`` is not legal for ``actor`` right now, or None when it is."""
        spec = self.contract.actions[name]
        if not actor.alive:
            return "you are no longer active"
        if spec.per_turn is not None and used_turn.get(name, 0) >= spec.per_turn:
            return f"{name} can be used {spec.per_turn} time(s) per turn"
        if spec.per_round is not None and used_round.get(name, 0) >= spec.per_round:
            return f"{name} can be used {spec.per_round} time(s) per round"
        refused = self._unmet(actor, name, self.world.scope(actor=actor), with_params=False)
        if refused is not None:
            return refused
        for pname, param in spec.params.items():
            if not self._required(param) or self._depends_on_params(param):
                continue
            if param.type == "entity" and not self._choices(actor, name, pname, param, first=True):
                return f"there is no {param.of or 'target'} you can choose for {pname} right now"
            if param.type in ("number", "int"):
                empty = self._empty_range(actor, param)
                if empty is not None:
                    return f"there is no valid {pname} right now ({empty})"
            if param.type == "enum" and isinstance(param.values, str) and self._static(actor, param.values) == []:
                return f"there is no value you can choose for {pname} right now"
        return None

    def _unmet(self, actor: Entity, name: str, scope: Any, with_params: bool) -> Optional[str]:
        """The `why` of the first requirement that does not hold: those over $actor alone, or those that read $params."""
        for index, condition in enumerate(self.contract.actions[name].when):
            compiled = compile_expr(condition.expr)
            if ("params" in compiled.roots) is not with_params:
                continue
            try:
                ok = truthy(compiled(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{name}.when[{index}]") from None
            if not ok:
                return (condition.why or "its requirements are not met").rstrip(". ")
        return None

    def _empty_range(self, actor: Entity, param: ParamSpec) -> Optional[str]:
        """The bounds, when no value lies between them right now (min above max)."""
        low, high = _tidy(self._static(actor, param.min)), _tidy(self._static(actor, param.max))
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (low, high)):
            return None
        least, most = (math.ceil(low), math.floor(high)) if param.type == "int" else (low, high)
        return f"at least {format_value(low)} and at most {format_value(high)}" if least > most else None

    @staticmethod
    def _required(param: ParamSpec) -> bool:
        return param.required if param.required is not None else param.default is None

    @staticmethod
    def _depends_on_params(param: ParamSpec) -> bool:
        return param.where is not None and "params" in compile_expr(param.where).roots

    def _choices(self, actor: Entity, action: str, pname: str, param: ParamSpec,
                 params: Optional[Dict[str, Any]] = None, first: bool = False) -> List[Entity]:
        """Entities that qualify. A `where` over earlier params is applied once they are known
        (at validation); before that (tool schemas) every entity of the type is listed. With
        ``first``, stop at the first one (enough to know whether any qualifies)."""
        if param.of is None:
            raise RunError("an entity parameter needs `of` (the entity type)", f"actions.{action}.params.{pname}")
        items = self.world.entities_of(param.of)
        if param.where is None:
            return items
        expr = compile_expr(param.where)
        if "params" in expr.roots and params is None:
            return items
        out = []
        base = self.world.scope(actor=actor, params=params or {})
        ruled_out = expr.rules_out(base)
        for position, item in enumerate(items):
            if ruled_out is not None and ruled_out(item):
                continue
            try:
                if truthy(expr(base.child(it=item, i=position))):
                    out.append(item)
                    if first:
                        break
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{action}.params.{pname}.where") from None
        return out

    # -- schemas ----------------------------------------------------------------

    def tools(self, actor: Entity, names: Sequence[str], staged: bool = False) -> List[ToolSpec]:
        """Tools for these legal actions: one per action, except that actions sharing a `tool` become one
        tool, placed where the first of them would be, whose `action` argument lists the legal ones."""
        slots: List[Any] = []
        shared: Dict[str, List[str]] = {}
        for name in names:
            group = self.contract.actions[name].tool
            if group is None:
                slots.append(self.tool(actor, name, staged))
            elif group in shared:
                shared[group].append(name)
            else:
                shared[group] = [name]
                slots.append(group)
        return [self.shared_tool(actor, slot, shared[slot], staged) if isinstance(slot, str) else slot for slot in slots]

    def shared_tool(self, actor: Entity, group: str, members: Sequence[str], staged: bool = False) -> ToolSpec:
        """One flat tool for several actions: ``action`` (required) picks one; every other argument belongs to
        the actions that take it, and the engine checks each action's own arguments when it is called."""
        choices = _choice_names(group, self.groups.get(group) or list(members))
        tools = [self.tool(actor, name) for name in members]
        properties: Dict[str, Any] = {"action": {
            "type": "string", "enum": [choices[t.name] for t in tools],
            "description": "One of the actions listed in this tool's description."}}
        takers: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for tool in tools:
            for pname, schema in tool.input_schema.get("properties", {}).items():
                takers.setdefault(pname, []).append((choices[tool.name], schema))
        for pname, entries in takers.items():
            properties[pname] = shared_param(entries, len(tools))
        schema = {"type": "object", "properties": properties, "required": ["action"], "additionalProperties": False}
        description = shared_description(group, [(choices[t.name], t.description, list(t.input_schema.get("properties", {})))
                                                 for t in tools], staged)
        return ToolSpec(group, description, schema, "act", all(t.terminal for t in tools))

    def route(self, group: str, args: Any, legal: Sequence[str]) -> Tuple[str, Dict[str, Any], Optional[str]]:
        """The action a call to a shared tool picks, with that action's own arguments — or a correction.

        Arguments the chosen action does not take are refused unless they are null (clients in strict
        mode send every property)."""
        members = self.groups[group]
        choices = _choice_names(group, members)
        open_now = ", ".join(choices[name] for name in members if name in legal) or "none right now"
        given = dict(args or {})
        picked = given.pop("action", None)
        if picked is None:
            return group, {}, f"{group} was not done: say which `action` to take (legal now: {open_now})."
        name = next((n for n in members if picked in (choices[n], n)), None)
        if name is None:
            return group, {}, f"{group} was not done: {_preview(picked)} is not one of its actions (legal now: {open_now})."
        params = self.contract.actions[name].params
        given = {key: value for key, value in given.items() if value is not None}
        extra = [str(key) for key in given if key not in params]
        if extra:
            takes = ", ".join(params) or "no other arguments"
            return group, {}, (f"{group} was not done: {choices[name]} does not take {', '.join(extra[:_LISTED_UNKNOWN])} "
                               f"(it takes: {takes}). Correct the arguments and call again.")
        return name, given, None

    def tool(self, actor: Entity, name: str, staged: bool = False) -> ToolSpec:
        spec = self.contract.actions[name]
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for pname, param in spec.params.items():
            properties[pname] = self._param_schema(actor, name, pname, param)
            if self._required(param):
                required.append(pname)
        schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        description = spec.description or name.replace("_", " ").capitalize() + "."
        if staged:
            description += " (Committed when everyone has chosen.)"
        if spec.terminal is True and "turn" not in description.lower():
            description += " Ends your turn."
        elif isinstance(spec.terminal, str):
            description += " May end your turn."
        limits = usage_limits(spec.per_turn, spec.per_round)
        if limits:
            description += " " + limits
        return ToolSpec(name, description, schema, "act", spec.terminal is True)

    def _static(self, actor: Entity, raw: Any) -> Any:
        """Evaluate a bound that depends only on the actor; None when it needs call arguments."""
        if not is_expr(raw):
            return raw
        expr = compile_expr(raw)
        if "params" in expr.roots:
            return None
        try:
            return expr(self.world.scope(actor=actor))
        except ExprError:
            return None

    def _param_schema(self, actor: Entity, action: str, pname: str, param: ParamSpec) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        description = param.description
        if param.type in ("number", "int"):
            out["type"] = "integer" if param.type == "int" else "number"
            for key, bound in (("minimum", param.min), ("maximum", param.max)):
                value = _tidy(self._static(actor, bound))
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    out[key] = math.ceil(value) if (param.type == "int" and key == "minimum") else (
                        math.floor(value) if param.type == "int" else value)
            if param.step is not None:
                base = out.get("minimum", 0) if param.min is not None else 0
                if param.min is None or "minimum" in out:
                    description = f"{description} In steps of {format_value(param.step)} from {format_value(base)}.".strip()
                if abs(base / param.step - round(base / param.step)) <= _STEP_TOLERANCE:
                    out["multipleOf"] = _tidy(param.step)
        elif param.type == "bool":
            out["type"] = "boolean"
        elif param.type == "file":
            out.update(file_schema(param))
            description = out.pop("description")
        elif param.type == "text":
            out["type"] = "string"
            out["maxLength"] = param.max_len if param.max_len is not None else TEXT_MAX_LEN
            if param.max_len is not None:
                description = f"{description or ''} {text_limit(param.max_len)}".strip()
        elif param.type == "enum":
            values = self._static(actor, param.values) if isinstance(param.values, str) else param.values
            if isinstance(values, list) and values:
                out["enum"] = [_plain(v) for v in values]
                if all(isinstance(v, str) for v in out["enum"]):
                    out["type"] = "string"
        elif param.type == "list":
            item = _item_spec(param)
            item_schema = self._param_schema(actor, action, pname, item)
            item_schema.pop("default", None)
            item_description = item_schema.pop("description", "")
            out["type"] = "array"
            out["items"] = item_schema
            low, high = _list_bounds(param)
            if low:
                out["minItems"] = low
            out["maxItems"] = high
            if param.unique:
                out["uniqueItems"] = True
            if item_description and item_description != description:
                description = f"{description} Each item: {item_description}".strip()
        elif param.type == "entity":
            out["type"] = "string"
            choices = self._choices(actor, action, pname, param)
            if self._depends_on_params(param):
                description = (description + " Valid choices depend on the other arguments.").strip()
            if len(choices) <= _ENUM_CHOICES and not self._depends_on_params(param):
                out["enum"] = [c.id for c in choices]
            if len(choices) <= _NAMED_CHOICES:
                listing = "; ".join(f"{c.id} = {c.name}" for c in choices if c.name != c.id)
                if listing:
                    description = f"{description} Options: {listing}.".strip()
            else:
                description = (description or f"Id of a {param.of}.").strip()
        if description:
            out["description"] = description
        default = self._schema_default(actor, param)
        if default is not None:
            out["default"] = default
        return out

    def _schema_default(self, actor: Entity, param: ParamSpec) -> Any:
        """The default as the agent would get it, or None when it cannot be known before the call
        (it reads other arguments) — never the raw expression text."""
        raw = param.default
        if raw is None:
            return None
        if _mentions_expr(raw):
            try:
                raw = resolve(raw, self.world.scope(actor=actor))
            except ExprError:
                return None
            if _mentions_expr(raw):
                return None
        value = _plain(raw)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return _tidy(value)

    # -- validation ---------------------------------------------------------------

    def validate(self, actor: Entity, name: str, args: Any) -> Tuple[Dict[str, Any], Optional[str]]:
        """Resolve arguments to typed values. Returns (params, None) or ({}, correction text)."""
        spec = self.contract.actions[name]
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {}, f"arguments must be an object of named arguments, got {_preview(args)}"
        problems: List[str] = []
        unknown = [key for key in args if not isinstance(key, str) or key not in spec.params]
        if unknown:
            listed = ", ".join(key if isinstance(key, str) and len(key) <= 60 else _preview(key)
                               for key in unknown[:_LISTED_UNKNOWN])
            if len(unknown) > _LISTED_UNKNOWN:
                listed += f" and {len(unknown) - _LISTED_UNKNOWN} more"
            problems.append(f"unknown argument(s) {listed} (arguments: {', '.join(spec.params) or 'none'})")
        params: Dict[str, Any] = {}
        seen: List[str] = []
        for pname, param in spec.params.items():
            failed = [p for p in seen if p not in params]  # earlier arguments that were wrong or missing
            seen.append(pname)
            raw = args.get(pname)
            if raw is None:
                if param.default is not None:
                    try:
                        raw = compile_expr(param.default)(self.world.scope(actor=actor, params=params)) \
                            if is_expr(param.default) else param.default
                    except ExprError as exc:
                        if failed:
                            problems.append(_waiting_on(pname, failed))
                            continue
                        raise RunError(str(exc), f"actions.{name}.params.{pname}.default") from None
                elif self._required(param):
                    problems.append(f"{pname} is required")
                    continue
                else:
                    params[pname] = None
                    continue
            try:
                value, problem = self._value(actor, name, pname, param, raw, params)
            except RunError:
                if not failed:
                    raise
                problems.append(_waiting_on(pname, failed))  # its bounds or choices read an argument that failed
                continue
            if problem and param.invalid:
                try:
                    shown = Untrusted(raw) if isinstance(raw, str) else raw
                    problem = compile_template(param.invalid, None).render(
                        self.world.scope(actor=actor, params=params, value=shown))
                except ExprError as exc:
                    raise RunError(str(exc), f"actions.{name}.params.{pname}.invalid") from None
                problems.append(problem.rstrip("."))
            elif problem:
                problems.append(f"{pname} {problem}")
            else:
                params[pname] = value
        if problems:
            return {}, "; ".join(problems)
        refused = self._unmet(actor, name, self.world.scope(actor=actor, params=params), with_params=True)
        if refused is not None:
            return {}, refused
        return params, None

    def _value(self, actor: Entity, action: str, pname: str, param: ParamSpec, raw: Any,
               params: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        kind = param.type
        if kind in ("number", "int"):
            value = _number_arg(raw)
            if value is None:
                return None, f"must be a number, got {_preview(raw)}"
            if isinstance(value, float) and not math.isfinite(value):
                return None, f"must be a finite number, got {_preview(raw)}"
            if abs(value) > MAX_SAFE_INT:
                return None, f"must be between -{MAX_SAFE_INT} and {MAX_SAFE_INT}"
            if kind == "int":
                if isinstance(value, float) and not value.is_integer():
                    return None, f"must be a whole number, got {_preview(raw) if isinstance(raw, str) else raw}"
                value = int(value)
            scope = self.world.scope(actor=actor, params=params)
            for label, bound, bad in (("at least", param.min, lambda v, b: v < b), ("at most", param.max, lambda v, b: v > b)):
                if bound is None:
                    continue
                try:
                    limit = compile_expr(bound)(scope) if is_expr(bound) else bound
                except ExprError as exc:
                    raise RunError(str(exc), f"actions.{action}.params.{pname}") from None
                if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))):
                    raise RunError(f"the {label} bound must be a number, got {format_value(limit)}",
                                   f"actions.{action}.params.{pname}")
                if limit is not None and bad(value, limit):
                    return None, f"must be {label} {format_value(limit)} (got {format_value(value)})"
            if param.step is not None:
                base = compile_expr(param.min)(scope) if is_expr(param.min) else param.min
                offset = (value - (base or 0)) / param.step
                if abs(offset - round(offset)) > _STEP_TOLERANCE:
                    return None, f"must go in steps of {format_value(param.step)} from {format_value(base or 0)} " \
                                 f"(got {format_value(value)})"
            return value, None
        if kind == "file":
            return file_value(self.world, param, raw)
        if kind == "bool":
            if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
                return raw.strip().lower() == "true", None
            if not isinstance(raw, bool):
                return None, f"must be true or false, got {_preview(raw)}"
            return raw, None
        if kind == "text":
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) \
                    or (isinstance(raw, float) and not math.isfinite(raw)) \
                    or (isinstance(raw, int) and abs(raw) > MAX_SAFE_INT):
                return None, f"must be text, got {_preview(raw)}"
            limit = param.max_len if param.max_len is not None else TEXT_MAX_LEN
            if isinstance(raw, str) and len(raw) > limit:
                return None, f"is {len(raw)} characters; the limit is {limit}"
            return Untrusted(str.__str__(raw) if isinstance(raw, str) else repr(raw)), None
        if kind == "enum":
            values = param.values
            if isinstance(values, str):
                try:
                    values = compile_expr(values)(self.world.scope(actor=actor, params=params))
                except ExprError as exc:
                    raise RunError(str(exc), f"actions.{action}.params.{pname}.values") from None
            if values is not None and not isinstance(values, (list, tuple)):
                raise RunError(f"values must give a list, got {format_value(values)}",
                               f"actions.{action}.params.{pname}.values")
            values = [_plain(v) for v in (values or [])]
            same = [v for v in values if v == raw and isinstance(v, bool) == isinstance(raw, bool)]
            if same:
                return same[0], None
            if isinstance(raw, str):
                folded = [v for v in values if isinstance(v, str) and v.lower() == raw.strip().lower()]
                if len(folded) == 1:
                    return folded[0], None
            return None, f"must be one of {', '.join(format_value(v) for v in values)} (got {_preview(raw)})"
        if kind == "list":
            return self._list_value(actor, action, pname, param, raw, params)
        if kind == "entity":
            chosen = self._chosen(actor, param, raw, params)
            if chosen is not None:
                return chosen, None
            choices = self._choices(actor, action, pname, param, params)
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                raw = raw["id"]
            if not isinstance(raw, str):
                return None, f"must be an id, got {_preview(raw)}"
            key = raw.strip()
            for choice in choices:
                if choice.id == key:
                    return choice, None
            by_name = [c for c in choices if (c.name or "").lower() == key.lower()]
            if len(by_name) == 1:
                return by_name[0], None
            listing = ", ".join(c.id for c in choices[:8]) + (" …" if len(choices) > 8 else "")
            shown = f"'{raw}'" if len(raw) <= 60 else _preview(raw)
            return None, f"{shown} is not a valid {param.of} here (valid: {listing or 'none'})"
        raise RunError(f"unknown parameter type '{kind}'", f"actions.{action}.params.{pname}")

    def _chosen(self, actor: Entity, param: ParamSpec, raw: Any, params: Dict[str, Any]) -> Optional[Entity]:
        """The entity an argument names by id when it plainly qualifies — found without listing every
        choice, which coded crowds would otherwise pay on every call. None sends the argument through the
        full listing, which decides every other case (names, refusals, errors) exactly as before."""
        key = raw["id"] if isinstance(raw, dict) and isinstance(raw.get("id"), str) else raw
        if param.of is None or param.of not in self.contract.types or not isinstance(key, str):
            return None
        entity = self.world.entities.get(key.strip())
        if entity is None or not entity.alive or not self.world.is_a(entity.entity_type, param.of):
            return None
        if param.where is None:
            return entity
        expr = compile_expr(param.where)
        if "i" in expr.roots or not nested_free():  # $i needs the full listing; nested work charges a budget
            return None
        world = self.world
        rng = world.rng
        state = rng.getstate()
        drawn = world.draws()
        try:
            holds = truthy(expr(world.scope(actor=actor, params=params).child(it=entity)))
        except ExprError:
            holds = False  # the full listing reports it
        if world.draws() != drawn:
            rng.setstate(state)  # the full listing makes every draw, in its own order
            return None
        return entity if holds else None

    def _list_value(self, actor: Entity, action: str, pname: str, param: ParamSpec, raw: Any,
                    params: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        if isinstance(raw, str):  # a model sometimes sends a list as JSON text or comma-separated words
            text = raw.strip()
            try:
                decoded = json.loads(text) if text.startswith("[") else None
            except ValueError:
                decoded = None
            raw = decoded if isinstance(decoded, list) else [part.strip() for part in text.split(",") if part.strip()]
        if not isinstance(raw, (list, tuple)):
            return None, f"must be a list, got {_preview(raw)}"
        low, high = _list_bounds(param)
        if len(raw) < low:
            return None, f"needs at least {low} item(s), got {len(raw)}"
        if len(raw) > high:
            return None, f"allows at most {high} item(s), got {len(raw)}"
        item = _item_spec(param)
        values: List[Any] = []
        seen: set = set()
        for index, element in enumerate(raw):
            value, problem = self._value(actor, action, pname, item, element, params)
            if problem:
                return None, f"item {index + 1} {problem}"
            key = getattr(value, "id", None) or json.dumps(_plain(value), sort_keys=True, default=str)
            if param.unique and key in seen:
                return None, f"lists {format_value(_plain(value))} more than once"
            seen.add(key)
            values.append(value)
        return values, None

    # -- apply ---------------------------------------------------------------------

    def apply(self, actor: Entity, name: str, params: Dict[str, Any]) -> Outcome:
        """Apply atomically. A `fail` effect or failed transfer rolls back and returns ok=False.
        Everything the action evaluates shares one work budget, so a loop of effects is bounded
        as a whole, not only each expression in it."""
        with shared_budget(ACTION_BUDGET, f"actions.{name}"):
            return self._apply(actor, name, params)

    def _apply(self, actor: Entity, name: str, params: Dict[str, Any]) -> Outcome:
        spec: ActionSpec = self.contract.actions[name]
        world = self.world
        mark = world.journal.mark()
        vars: Dict[str, Any] = {"actor": actor, "params": params}
        path = f"actions.{name}"
        success = True
        log_mark = world.log[-1].seq if world.log else 0
        record_mark = world._record_seq
        try:
            if spec.chance is not None:
                probability = compile_expr(spec.chance)(world.scope(**vars)) if is_expr(spec.chance) else spec.chance
                if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                    raise RunError(f"chance must be a number, got {probability!r}", f"{path}.chance")
                success = world.rng.random() < probability
            self.effects.run(spec.do if success else spec.otherwise, vars, f"{path}.{'do' if success else 'otherwise'}")
            text = self._render(spec.outcome, vars, f"{path}.outcome") if spec.outcome else self._default_outcome(name, params, success)
            assets = attached_ids(world, spec.attach, world.scope(**vars), f"{path}.attach") if spec.attach else []
            announce = spec.announce
            if not spec.private:
                public = self._public_params(params, self._posted_since(record_mark))
                if announce is not None:
                    line = self._render(announce, vars, f"{path}.announce")
                elif _notified_since(world, log_mark):
                    line = ""  # the posted entry itself is the news
                else:
                    line = self._default_announce(actor, name, public, success)
                # Public: every agent may learn of it; the actor's own announcement is
                # filtered out of its news by perception.
                announcement = world.emit("action", line, actor=actor.id, to=None,
                                          data={"action": name, "params": _plain(public), "success": success})
                _first_in_order(world, log_mark, announcement)
            else:
                world.emit("action", "", actor=actor.id, to=(actor.id,),
                           data={"action": name, "params": _plain(params), "success": success, "private": True})
        except Abort as abort:
            world.journal.rollback(mark)
            return Outcome(False, abort.reason, False, params)
        except ExprError as exc:
            world.journal.rollback(mark)
            raise RunError(str(exc), path) from None
        except RunError:
            world.journal.rollback(mark)
            raise
        return Outcome(True, text, success, params, assets)

    def duration(self, actor: Entity, name: str, params: Dict[str, Any]) -> float:
        """How long the action takes on a continuous clock (0 when it declares no duration)."""
        raw = self.contract.actions[name].duration
        if raw is None:
            return 0.0
        try:
            value = compile_expr(raw)(self.world.scope(actor=actor, params=params)) if is_expr(raw) else raw
        except ExprError as exc:
            raise RunError(str(exc), f"actions.{name}.duration") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise RunError(f"duration must be a number ≥ 0, got {format_value(value)}", f"actions.{name}.duration")
        return float(value)

    def ends_turn(self, actor: Entity, name: str, params: Dict[str, Any]) -> bool:
        terminal = self.contract.actions[name].terminal
        if isinstance(terminal, bool):
            return terminal
        try:
            return truthy(compile_expr(terminal)(self.world.scope(actor=actor, params=params)))
        except ExprError as exc:
            raise RunError(str(exc), f"actions.{name}.terminal") from None

    def dry_run(self, actor: Entity, name: str, params: Dict[str, Any]) -> Optional[str]:
        """Apply and roll back, to catch a doomed sealed choice at submit. Returns the refusal, or None."""
        world = self.world
        mark = world.journal.mark()
        rng_state = world.rng.getstate()
        picker, world.chance_picker = world.chance_picker, None  # a trial roll is sampled, never asked for
        try:
            outcome = self.apply(actor, name, params)
        finally:
            world.journal.rollback(mark)
            world.rng.setstate(rng_state)
            world.chance_picker = picker
        return None if outcome.ok else outcome.text

    def _posted_since(self, record_mark: int) -> List[Tuple[RecordSpec, Dict[str, Any]]]:
        """Entries posted after ``record_mark``, with their record's spec."""
        if self.world._record_seq == record_mark:
            return []
        posted: List[Tuple[RecordSpec, Dict[str, Any]]] = []
        for name, spec in self.contract.records.items():
            for entry in reversed(self.world.records_store.get(name, [])):
                if entry["seq"] <= record_mark:
                    break
                posted.append((spec, entry))
        return posted

    @staticmethod
    def _public_params(params: Dict[str, Any], posted: Sequence[Tuple[RecordSpec, Dict[str, Any]]]) -> Dict[str, Any]:
        """The arguments an announcement may repeat. An entry that is not broadcast to everyone
        (a record that does not notify, a directed or restricted entry) keeps its content to
        its own audience, so arguments carried into it are left out."""
        kept = [entry.get(field) for spec, entry in posted
                if not spec.notify or entry.get("to") is not None or spec.visible != "all"
                for field in spec.fields]
        if not kept:
            return params
        return {k: v for k, v in params.items() if not _carried(_plain(v), kept)}

    def _render(self, template: str, vars: Dict[str, Any], path: str) -> str:
        try:
            return compile_template(template, None).render(self.world.scope(**vars))
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    @staticmethod
    def _args_text(params: Dict[str, Any]) -> str:
        parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
        return f" ({', '.join(parts)})" if parts else ""

    def _default_outcome(self, name: str, params: Dict[str, Any], success: bool) -> str:
        verb = name.replace("_", " ")
        return f"Done: {verb}{self._args_text(params)}." if success else f"{verb.capitalize()} did not succeed."

    def _default_announce(self, actor: Entity, name: str, params: Dict[str, Any], success: bool) -> str:
        verb = name.replace("_", " ")
        suffix = "" if success else " — it did not succeed"
        return f"{actor.name}: {verb}{self._args_text(params)}{suffix}."


def _choice_names(group: str, members: Sequence[str]) -> Dict[str, str]:
    """How each action is named inside its shared tool: without the tool's name as a prefix when that stays unambiguous."""
    prefix = f"{group}_"
    short = {name: name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else name for name in members}
    taken = list(short.values())
    return {name: s if taken.count(s) == 1 and (s == name or s not in members) else name for name, s in short.items()}


def _item_spec(param: ParamSpec) -> ParamSpec:
    """The element spec of a list parameter: explicit `items`, or `of` / `values` shorthand."""
    if param.items is not None:
        return param.items
    if param.of is not None:
        return ParamSpec(type="entity", of=param.of, where=param.where)
    if param.values is not None:
        return ParamSpec(type="enum", values=param.values)
    return ParamSpec(type="text", max_len=param.max_len)


def _list_bounds(param: ParamSpec) -> Tuple[int, int]:
    low = param.min_items or 0
    high = min(param.max_items if param.max_items is not None else MAX_LIST_ITEMS, MAX_LIST_ITEMS)
    return low, high


def _number_arg(raw: Any) -> Any:
    """``raw`` as a number (numeric text is read), or None when it is not one."""
    if isinstance(raw, str):
        text = raw.strip()
        if len(text) > _NUMBER_TEXT:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw


def _mentions_expr(value: Any) -> bool:
    """True when ``value`` still holds expression or template text anywhere inside it."""
    if isinstance(value, str):
        return bool(_LEFTOVER_EXPR.search(value)) or is_expr(value)
    if isinstance(value, (list, tuple)):
        return any(_mentions_expr(item) for item in value)
    if isinstance(value, dict):
        return any(_mentions_expr(item) for item in value.values())
    return False


def _carried(value: Any, fields: Sequence[Any]) -> bool:
    """True when an argument value (or text containing it) is stored in one of ``fields``."""
    if value is None or isinstance(value, bool) or value == "":
        return False
    for stored in fields:
        if stored == value:
            return True
        if isinstance(value, str) and isinstance(stored, str) and value in stored:
            return True
        if isinstance(stored, (list, tuple)) and any(_carried(value, [item]) for item in stored):
            return True
    return False


def _notified_since(world: SdkWorld, log_mark: int) -> bool:
    """True when a record entry was delivered as news after log position ``log_mark``."""
    for event in reversed(world.log):
        if event.seq <= log_mark:
            return False
        if event.kind == "record":
            return True
    return False


def _first_in_order(world: SdkWorld, log_mark: int, event: Any) -> None:
    """Move an action's announcement ahead of the news its own effects produced."""
    log = world.log
    index = len(log) - 1
    while index > 0 and log[index - 1].seq > log_mark:
        index -= 1
    if log[index] is event:
        return
    log.remove(event)
    log.insert(index, event)
    for offset, item in enumerate(log[index:]):
        item.seq = log_mark + 1 + offset


def _waiting_on(pname: str, failed: List[str]) -> str:
    return f"{pname} can be checked once {', '.join(failed)} {'is' if len(failed) == 1 else 'are'} corrected"
