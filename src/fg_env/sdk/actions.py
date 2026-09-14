"""Actions as tools: which are legal, their JSON Schemas, argument validation, atomic apply."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..entity import Entity
from .contract import ActionSpec, Contract, ParamSpec, StageSpec
from .effects import EffectRunner
from .errors import RunError
from .expr import ExprError, compile_expr, is_expr, truthy
from .template import compile_template, format_value
from .world import Abort, SdkWorld, _plain

__all__ = ["ToolSpec", "Outcome", "ActionBook", "stage_actions"]

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


def stage_actions(contract: Contract, stage: StageSpec, type_name: str) -> List[str]:
    """Action names available to ``type_name`` during ``stage`` (before per-turn legality)."""
    spec = stage.actions
    if spec == "all":
        names = list(contract.actions)
    elif isinstance(spec, list):
        names = list(spec)
    elif isinstance(spec, dict):
        names = list(spec.get(type_name, []))
    else:
        names = []
    out = []
    for name in names:
        action = contract.actions.get(name)
        if action is None:
            continue
        by = [action.by] if isinstance(action.by, str) else action.by
        if type_name in by:
            out.append(name)
    return out


class ActionBook:
    def __init__(self, contract: Contract, world: SdkWorld, effects: EffectRunner):
        self.contract = contract
        self.world = world
        self.effects = effects

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
        scope = self.world.scope(actor=actor)
        for index, condition in enumerate(spec.when):
            try:
                ok = truthy(compile_expr(condition.expr)(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{name}.when[{index}]") from None
            if not ok:
                return condition.why or "its requirements are not met"
        for pname, param in spec.params.items():
            if param.type == "entity" and self._required(param) and not self._choices(actor, name, pname, param):
                return f"there is no valid {param.of or 'target'} for {pname}"
        return None

    @staticmethod
    def _required(param: ParamSpec) -> bool:
        return param.required if param.required is not None else param.default is None

    def _choices(self, actor: Entity, action: str, pname: str, param: ParamSpec) -> List[Entity]:
        if param.of is None:
            raise RunError("an entity parameter needs `of` (the entity type)", f"actions.{action}.params.{pname}")
        items = self.world.entities_of(param.of)
        if param.where is None:
            return items
        expr = compile_expr(param.where)
        out = []
        for position, item in enumerate(items):
            try:
                if truthy(expr(self.world.scope(actor=actor, it=item, i=position))):
                    out.append(item)
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{action}.params.{pname}.where") from None
        return out

    # -- schemas ----------------------------------------------------------------

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
        if spec.terminal:
            description += " Ends your turn."
        return ToolSpec(name, description, schema, "act", spec.terminal)

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
                value = self._static(actor, bound)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    out[key] = math.ceil(value) if (param.type == "int" and key == "minimum") else (
                        math.floor(value) if param.type == "int" else value)
        elif param.type == "bool":
            out["type"] = "boolean"
        elif param.type == "text":
            out["type"] = "string"
            if param.max_len:
                out["maxLength"] = param.max_len
        elif param.type == "enum":
            values = self._static(actor, param.values) if isinstance(param.values, str) else param.values
            if isinstance(values, list) and values:
                out["enum"] = [_plain(v) for v in values]
                if all(isinstance(v, str) for v in out["enum"]):
                    out["type"] = "string"
        elif param.type == "entity":
            out["type"] = "string"
            choices = self._choices(actor, action, pname, param)
            if len(choices) <= _ENUM_CHOICES:
                out["enum"] = [c.id for c in choices]
            if len(choices) <= _NAMED_CHOICES:
                listing = "; ".join(f"{c.id} = {c.name}" for c in choices if c.name != c.id)
                if listing:
                    description = f"{description} Options: {listing}.".strip()
            else:
                description = (description or f"Id of a {param.of}.").strip()
        if description:
            out["description"] = description
        if param.default is not None and not is_expr(param.default):
            out["default"] = param.default
        return out

    # -- validation ---------------------------------------------------------------

    def validate(self, actor: Entity, name: str, args: Any) -> Tuple[Dict[str, Any], Optional[str]]:
        """Resolve arguments to typed values. Returns (params, None) or ({}, correction text)."""
        spec = self.contract.actions[name]
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {}, "arguments must be an object"
        problems: List[str] = []
        unknown = [key for key in args if key not in spec.params]
        if unknown:
            problems.append(
                f"unknown argument(s) {', '.join(unknown)} (arguments: {', '.join(spec.params) or 'none'})"
            )
        params: Dict[str, Any] = {}
        for pname, param in spec.params.items():
            raw = args.get(pname)
            if raw is None:
                if param.default is not None:
                    try:
                        raw = compile_expr(param.default)(self.world.scope(actor=actor, params=params)) \
                            if is_expr(param.default) else param.default
                    except ExprError as exc:
                        raise RunError(str(exc), f"actions.{name}.params.{pname}.default") from None
                elif self._required(param):
                    problems.append(f"{pname} is required")
                    continue
                else:
                    params[pname] = None
                    continue
            value, problem = self._value(actor, name, pname, param, raw, params)
            if problem:
                problems.append(f"{pname} {problem}")
            else:
                params[pname] = value
        if problems:
            return {}, "; ".join(problems)
        return params, None

    def _value(self, actor: Entity, action: str, pname: str, param: ParamSpec, raw: Any,
               params: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        kind = param.type
        if kind in ("number", "int"):
            value = raw
            if isinstance(value, str):
                try:
                    value = float(value.strip())
                except ValueError:
                    return None, f"must be a number, got {raw!r}"
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return None, f"must be a number, got {raw!r}"
            if kind == "int":
                if float(value) != int(value):
                    return None, f"must be a whole number, got {raw}"
                value = int(value)
            scope = self.world.scope(actor=actor, params=params)
            for label, bound, bad in (("at least", param.min, lambda v, b: v < b), ("at most", param.max, lambda v, b: v > b)):
                if bound is None:
                    continue
                try:
                    limit = compile_expr(bound)(scope) if is_expr(bound) else bound
                except ExprError as exc:
                    raise RunError(str(exc), f"actions.{action}.params.{pname}") from None
                if limit is not None and bad(value, limit):
                    return None, f"must be {label} {format_value(limit)} (got {format_value(value)})"
            return value, None
        if kind == "bool":
            if isinstance(raw, str) and raw.lower() in ("true", "false"):
                return raw.lower() == "true", None
            if not isinstance(raw, bool):
                return None, f"must be true or false, got {raw!r}"
            return raw, None
        if kind == "text":
            if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
                return None, f"must be text, got {raw!r}"
            text = str(raw)
            if param.max_len is not None and len(text) > param.max_len:
                return None, f"is {len(text)} characters; the limit is {param.max_len}"
            return text, None
        if kind == "enum":
            values = param.values
            if isinstance(values, str):
                try:
                    values = compile_expr(values)(self.world.scope(actor=actor, params=params))
                except ExprError as exc:
                    raise RunError(str(exc), f"actions.{action}.params.{pname}.values") from None
            values = [_plain(v) for v in (values or [])]
            if raw in values:
                return raw, None
            if isinstance(raw, str):
                folded = [v for v in values if isinstance(v, str) and v.lower() == raw.strip().lower()]
                if len(folded) == 1:
                    return folded[0], None
            return None, f"must be one of {', '.join(format_value(v) for v in values)} (got {raw!r})"
        if kind == "entity":
            choices = self._choices(actor, action, pname, param)
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                raw = raw["id"]
            if not isinstance(raw, str):
                return None, f"must be an id, got {raw!r}"
            key = raw.strip()
            for choice in choices:
                if choice.id == key:
                    return choice, None
            by_name = [c for c in choices if c.name.lower() == key.lower()]
            if len(by_name) == 1:
                return by_name[0], None
            listing = ", ".join(c.id for c in choices[:8]) + (" …" if len(choices) > 8 else "")
            return None, f"'{raw}' is not a valid {param.of} here (valid: {listing or 'none'})"
        raise RunError(f"unknown parameter type '{kind}'", f"actions.{action}.params.{pname}")

    # -- apply ---------------------------------------------------------------------

    def apply(self, actor: Entity, name: str, params: Dict[str, Any]) -> Outcome:
        """Apply atomically. A `fail` effect or failed transfer rolls back and returns ok=False."""
        spec: ActionSpec = self.contract.actions[name]
        world = self.world
        mark = world.journal.mark()
        vars: Dict[str, Any] = {"actor": actor, "params": params}
        path = f"actions.{name}"
        success = True
        try:
            if spec.chance is not None:
                probability = compile_expr(spec.chance)(world.scope(**vars)) if is_expr(spec.chance) else spec.chance
                if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                    raise RunError(f"chance must be a number, got {probability!r}", f"{path}.chance")
                success = world.rng.random() < probability
            self.effects.run(spec.do if success else spec.otherwise, vars, f"{path}.{'do' if success else 'otherwise'}")
            text = self._render(spec.outcome, vars, f"{path}.outcome") if spec.outcome else self._default_outcome(name, params, success)
            announce = spec.announce
            if not spec.private:
                line = self._render(announce, vars, f"{path}.announce") if announce is not None else \
                    self._default_announce(actor, name, params, success)
                everyone_else = tuple(e.id for e in world.entities.values() if e.id != actor.id) if line else ()
                world.emit("action", line, actor=actor.id, to=everyone_else,
                           data={"action": name, "params": _plain(params), "success": success})
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
        return Outcome(True, text, success, params)

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
