"""Actions as tools: the JSON Schema of each legal action, shared tools that group several actions, and routing
a shared tool's call to the action it picks."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from ..entity import Entity
from .action_params import TEXT_MAX_LEN, _LISTED_UNKNOWN, _STEP_TOLERANCE, _item_spec, _list_bounds, _preview, _tidy
from .assets.intake import file_schema
from .contract import ParamSpec
from .expr import ExprError, compile_expr, is_expr, resolve
from .tool_text import compact_ids, shared_description, shared_param, text_limit, usage_limits
from .world import _plain

if TYPE_CHECKING:
    from .actions import ActionBook

__all__ = ["ToolSpec", "ActionSchemas"]

_LEFTOVER_EXPR = re.compile(r"\$['\"(A-Za-z_]")
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


class ActionSchemas:
    """Tool schemas for an actor's legal actions (mixed into :class:`~fg_env.sdk.actions.ActionBook`)."""

    def tools(self: "ActionBook", actor: Entity, names: Sequence[str], staged: bool = False) -> List[ToolSpec]:  # type: ignore[misc]
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

    def shared_tool(self: "ActionBook", actor: Entity, group: str, members: Sequence[str], staged: bool = False) -> ToolSpec:  # type: ignore[misc]
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

    def route(self: "ActionBook", group: str, args: Any, legal: Sequence[str]) -> Tuple[str, Dict[str, Any], Optional[str]]:  # type: ignore[misc]
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

    def tool(self: "ActionBook", actor: Entity, name: str, staged: bool = False) -> ToolSpec:  # type: ignore[misc]
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

    def _static(self: "ActionBook", actor: Entity, raw: Any) -> Any:  # type: ignore[misc]
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

    def _param_schema(self: "ActionBook", actor: Entity, action: str, pname: str, param: ParamSpec) -> Dict[str, Any]:  # type: ignore[misc]
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
                    description = f"{description} In steps of {_preview(_tidy(param.step))} from {_preview(_tidy(base))}.".strip()
                    if abs(base / param.step - round(base / param.step)) <= _STEP_TOLERANCE:
                        out["multipleOf"] = _tidy(param.step)
                else:
                    description = (f"{description} In steps of {_preview(_tidy(param.step))} from {param.min} "
                                   "(resolved from the action arguments).").strip()
        elif param.type == "bool":
            out["type"] = "boolean"
        elif param.type == "file":
            out.update(file_schema(param))
            description = out.pop("description")
        elif param.type == "text":
            out["type"] = "string"
            out["maxLength"] = param.max_len if param.max_len is not None else TEXT_MAX_LEN
            if param.max_len is not None:
                description = f"{description or ''} {text_limit(param.max_len, param.overflow)}".strip()
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
            choices = self._choices(actor, action, pname, param)  # every candidate when they depend on other arguments
            if self._depends_on_params(param):
                description = (description + " Valid choices depend on the other arguments.").strip()
            if len(choices) <= _ENUM_CHOICES:
                out["enum"] = [c.id for c in choices]
            if len(choices) <= _NAMED_CHOICES:
                listing = "; ".join(f"{c.id} = {c.name}" for c in choices if c.name != c.id)
                if listing:
                    description = f"{description} Options: {listing}.".strip()
            else:
                description = (description or f"Id of a {param.of}.").strip()
                if "enum" not in out:
                    description += f" One of: {compact_ids([c.id for c in choices])}."
        if description:
            out["description"] = description
        default = self._schema_default(actor, param)
        if default is not None:
            out["default"] = default
        return out

    def _schema_default(self: "ActionBook", actor: Entity, param: ParamSpec) -> Any:  # type: ignore[misc]
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


def _choice_names(group: str, members: Sequence[str]) -> Dict[str, str]:
    """How each action is named inside its shared tool: without the tool's name as a prefix when that stays unambiguous."""
    prefix = f"{group}_"
    short = {name: name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else name for name in members}
    taken = list(short.values())
    return {name: s if taken.count(s) == 1 and (s == name or s not in members) else name for name, s in short.items()}


def _mentions_expr(value: Any) -> bool:
    """True when ``value`` still holds expression or template text anywhere inside it."""
    if isinstance(value, str):
        return bool(_LEFTOVER_EXPR.search(value)) or is_expr(value)
    if isinstance(value, (list, tuple)):
        return any(_mentions_expr(item) for item in value)
    if isinstance(value, dict):
        return any(_mentions_expr(item) for item in value.values())
    return False
