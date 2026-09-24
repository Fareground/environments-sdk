"""Actions as tools: the JSON Schema of each legal action, shared tools that group several actions, and routing
a shared tool's call to the action it picks."""
from __future__ import annotations

import itertools
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..assets.intake import file_schema
from ..contract import ParamSpec
from ..errors import RunError
from ..expr import ExprError, PrivateRead, compile_expr, is_expr, resolve
from ..expr.objects import Entity
from ..world.live import _copy, _plain
from .params import (
    _STEP_TOLERANCE,
    TEXT_MAX_LEN,
    _item_count,
    _item_spec,
    _list_bounds,
    _preview,
    _tidy,
)
from .tool_text import compact_ids, text_limit, usage_limits

if TYPE_CHECKING:
    from .book import ActionBook

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
    input_schema: dict[str, Any]
    kind: str = "act"  # act | look | end
    terminal: bool = False

    def copy(self) -> ToolSpec:
        """The tool with a schema of its own, for a caller that may change it (a schema is plain JSON data)."""
        return ToolSpec(self.name, self.description, _copy(self.input_schema), self.kind, self.terminal)

    def to_anthropic(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def to_openai(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.input_schema,
        }}

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "kind": self.kind, "terminal": self.terminal}


class ActionSchemas:
    """Tool schemas for an actor's legal actions (mixed into :class:`~fg_env.actions.book.ActionBook`)."""

    def tools(self: ActionBook, actor: Entity, names: Sequence[str],  # type: ignore[misc]
              staged: bool = False) -> list[ToolSpec]:
        """One tool for each of these legal actions."""
        return [self.tool(actor, name, staged) for name in names]

    def tool(self: ActionBook, actor: Entity, name: str, staged: bool = False) -> ToolSpec:  # type: ignore[misc]
        # A copy: callers may change the schema they are given (the remembered one is listed again this turn).
        with self.deciding():
            key = ("tool", actor.id, name, staged)
            return self.world.remembered(key, lambda: self._tool(actor, name, staged)).copy()

    def _tool(self: ActionBook, actor: Entity, name: str, staged: bool) -> ToolSpec:  # type: ignore[misc]
        spec = self.contract.actions[name]
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in spec.params.items():
            properties[pname] = self._param_schema(actor, name, pname, param)
            if self._required(param):
                required.append(pname)
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        description = spec.description or name.replace("_", " ").capitalize() + "."
        if staged:
            description += " (Committed when everyone has chosen.)"
        if spec.terminal is True and "ends your turn" not in description.lower():  # never said twice
            description += " Ends your turn."
        elif isinstance(spec.terminal, str):
            description += " May end your turn."
        limits = usage_limits(spec.per_turn, spec.per_round)
        if limits:
            description += " " + limits
        return ToolSpec(name, description, schema, "act", spec.terminal is True)

    def _static(self: ActionBook, actor: Entity, raw: Any, where: str) -> Any:  # type: ignore[misc]
        """Evaluate a bound that depends only on the actor; None when it needs call arguments. One that reads another
        agent's private property is an error at ``where``: the tool would be offered without it, and refused."""
        if not is_expr(raw):
            return raw
        expr = compile_expr(raw)
        if "params" in expr.roots:
            return None
        try:
            return expr(self.world.scope(actor=actor, viewer=actor))
        except PrivateRead as exc:
            raise RunError(str(exc), where) from None
        except ExprError:
            return None

    def _param_schema(self: ActionBook, actor: Entity, action: str, pname: str,  # type: ignore[misc]
                      param: ParamSpec) -> dict[str, Any]:
        out: dict[str, Any] = {}
        description = param.description
        if param.type in ("number", "int"):
            out["type"] = "integer" if param.type == "int" else "number"
            for key, field in (("minimum", "min"), ("maximum", "max")):
                value = _tidy(self._static(actor, getattr(param, field), f"actions.{action}.params.{pname}.{field}"))
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    out[key] = math.ceil(value) if (param.type == "int" and key == "minimum") else (
                        math.floor(value) if param.type == "int" else value)
            if param.step is not None:
                base = out.get("minimum", 0) if param.min is not None else 0
                if param.min is None or "minimum" in out:
                    description = (f"{description} In steps of {_preview(_tidy(param.step))} from "
                                   f"{_preview(_tidy(base))}.").strip()
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
            values = self._static(actor, param.values, f"actions.{action}.params.{pname}.values") \
                if isinstance(param.values, str) else param.values
            if values is None and self._values_depend_on_params(param):
                values = self._every_value(actor, action, pname, param)
                description = (description + " Valid choices depend on the other arguments.").strip()
            if isinstance(values, list) and values:
                out["enum"] = [_plain(v) for v in values]
                kind = _enum_type(out["enum"])
                if kind:
                    out["type"] = kind
        elif param.type == "list":
            item = _item_spec(param)
            item_schema = self._param_schema(actor, action, pname, item)
            item_schema.pop("default", None)
            item_description = item_schema.pop("description", "")
            out["type"] = "array"
            out["items"] = item_schema
            where = f"actions.{action}.params.{pname}"
            low, high = _list_bounds(param, lambda raw, key: _item_count(self._static(actor, raw, f"{where}.{key}"),
                                                                         f"{where}.{key}"))
            if low:
                out["minItems"] = low
            if high is not None:
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

    def _every_value(self: ActionBook, actor: Entity, action: str, pname: str,  # type: ignore[misc]
                     param: ParamSpec) -> list[Any] | None:
        """Every value an enum's `values` over earlier arguments can give, over each choice those arguments offer
        (so the tool lists real options; validation enforces the ones that fit the arguments given). None when the
        earlier arguments cannot all be listed, or there are too many combinations to try."""
        spec = self.contract.actions[action]
        earlier = list(spec.params)[:list(spec.params).index(pname)]
        axes: dict[str, list[Any]] = {}
        for name in earlier:
            before = spec.params[name]
            if before.type == "entity":
                axes[name] = list(self._choices(actor, action, name, before))
            elif before.type == "enum" and isinstance(before.values, list):
                axes[name] = list(before.values)
            elif before.type == "bool":
                axes[name] = [False, True]
            else:
                return None
        if math.prod(len(options) for options in axes.values()) > _ENUM_CHOICES:
            return None
        found: list[Any] = []
        for combination in itertools.product(*axes.values()):
            try:
                values = self.enum_values(actor, action, pname, param, dict(zip(axes, combination)))
            except RunError:
                continue  # a combination the values cannot be worked out for offers nothing
            found.extend(value for value in values if value not in found)
        return found if len(found) <= _ENUM_CHOICES else None

    def _schema_default(self: ActionBook, actor: Entity, param: ParamSpec) -> Any:  # type: ignore[misc]
        """The default as the agent would get it, or None when it cannot be known before the call
        (it reads other arguments) — never the raw expression text."""
        raw = param.default
        if raw is None:
            return None
        if _mentions_expr(raw):
            try:
                raw = resolve(raw, self.world.scope(actor=actor, viewer=actor))
            except ExprError:
                return None
            if _mentions_expr(raw):
                return None
        value = _plain(raw)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return _tidy(value)


def _enum_type(values: Sequence[Any]) -> str | None:
    """The JSON type every enum value shares (some providers refuse an enum without one); None when they are mixed."""
    kinds = {"boolean" if isinstance(v, bool) else "integer" if isinstance(v, int) else "number" if isinstance(v, float)
             else "string" if isinstance(v, str) else "other" for v in values}
    if kinds == {"integer", "number"}:
        return "number"
    return next(iter(kinds)) if len(kinds) == 1 and "other" not in kinds else None


def _mentions_expr(value: Any) -> bool:
    """True when ``value`` still holds expression or template text anywhere inside it."""
    if isinstance(value, str):
        return bool(_LEFTOVER_EXPR.search(value)) or is_expr(value)
    if isinstance(value, (list, tuple)):
        return any(_mentions_expr(item) for item in value)
    if isinstance(value, dict):
        return any(_mentions_expr(item) for item in value.values())
    return False
