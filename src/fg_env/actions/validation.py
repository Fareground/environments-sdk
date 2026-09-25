"""Argument validation: resolving what a participant passed to an action into typed values, or a correction."""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

from ..assets.intake import file_value
from ..contract import ParamSpec
from ..errors import RunError
from ..expr import ExprError, Scope, Untrusted, compile_expr, is_expr, nested_free, truthy
from ..expr.objects import Entity
from ..expr.template import format_value
from ..information.gate import render, viewer_for
from ..world.values import plain_value
from .params import (
    _LISTED_UNKNOWN,
    MAX_SAFE_INT,
    STEP_TOLERANCE,
    TEXT_MAX_LEN,
    item_count,
    item_spec,
    list_bounds,
    preview,
)

if TYPE_CHECKING:
    from .book import ActionBook

__all__ = ["ActionValidation"]

#: Longest text read as a number (`"12.5"`); anything longer is not a number.
_NUMBER_TEXT = 64


class ActionValidation:
    """Argument validation for the actions of ``book`` (its :attr:`~fg_env.actions.book.ActionBook.validation`)."""

    def __init__(self, book: ActionBook):
        self.book = book
        self.contract = book.contract
        self.world = book.world

    def validate(self, actor: Entity, name: str, args: Any) -> tuple[dict[str, Any], str | None]:
        """Resolve arguments to typed values. Returns (params, None) or ({}, correction text). A coded policy checks
        its call before making it, so the answer is remembered for the same arguments in the same state."""
        if not isinstance(args, dict):
            return self._validate(actor, name, args)
        with self.book.deciding():
            params, problem = self.world.evaluation.remembered(("valid", actor.id, name, repr(args)),
                                                               lambda: self._validate(actor, name, args))
        return dict(params), problem

    def _validate(self, actor: Entity, name: str, args: Any) -> tuple[dict[str, Any], str | None]:
        spec = self.contract.actions[name]
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {}, f"arguments must be an object of named arguments, got {preview(args)}"
        problems: list[str] = []
        unknown = [key for key in args if not isinstance(key, str) or key not in spec.params]
        if unknown:
            listed = ", ".join(key if isinstance(key, str) and len(key) <= 60 else preview(key)
                               for key in unknown[:_LISTED_UNKNOWN])
            if len(unknown) > _LISTED_UNKNOWN:
                listed += f" and {len(unknown) - _LISTED_UNKNOWN} more"
            problems.append(f"unknown argument(s) {listed} (arguments: {', '.join(spec.params) or 'none'})")
        params: dict[str, Any] = {}
        seen: list[str] = []
        for pname, param in spec.params.items():
            failed = [p for p in seen if p not in params]  # earlier arguments that were wrong or missing
            seen.append(pname)
            raw = args.get(pname)
            if raw is None:
                if param.default is not None:
                    try:
                        raw = compile_expr(param.default)(self.world.evaluation.scope(
                            actor=actor, viewer=viewer_for("ParamSpec.default", actor), params=params)) \
                            if is_expr(param.default) else param.default
                    except ExprError as exc:
                        if failed:
                            problems.append(_waiting_on(pname, failed))
                            continue
                        raise RunError(str(exc), f"actions.{name}.params.{pname}.default") from None
                elif self.book.required(param):
                    problems.append(f"{pname} is required")
                    continue
                else:
                    params[pname] = None
                    continue
            try:
                value, problem = self.value(actor, name, pname, param, raw, params)
            except RunError:
                if not failed:
                    raise
                problems.append(_waiting_on(pname, failed))  # its bounds or choices read an argument that failed
                continue
            if problem and param.invalid:
                shown = Untrusted(raw) if isinstance(raw, str) else raw
                problem = render(self.world, param.invalid, {"actor": actor, "params": params, "value": shown},
                                 viewer=viewer_for("ParamSpec.invalid", actor),
                                 path=f"actions.{name}.params.{pname}.invalid")
                problems.append(problem.rstrip("."))
            elif problem:
                problems.append(f"{pname} {problem}")
            else:
                params[pname] = value
        if problems:
            return {}, "; ".join(problems)
        refused = self.book.unmet(actor, name, params)
        if refused is not None:
            return {}, refused
        return params, None

    def value(self, actor: Entity, action: str, pname: str, param: ParamSpec,
               raw: Any,
               params: dict[str, Any]) -> tuple[Any, str | None]:
        kind = param.type
        if kind in ("number", "int"):
            value = _number_arg(raw)
            if value is None:
                return None, f"must be a number, got {preview(raw)}"
            if isinstance(value, float) and not math.isfinite(value):
                return None, f"must be a finite number, got {preview(raw)}"
            if abs(value) > MAX_SAFE_INT:
                return None, f"is far too large ({preview(raw)}): numbers here stay within ±{MAX_SAFE_INT:,}"
            if kind == "int":
                if isinstance(value, float) and not value.is_integer():
                    return None, f"must be a whole number, got {preview(raw) if isinstance(raw, str) else raw}"
                value = int(value)
            scope: Scope | None = None  # built only for a bound that is an expression
            for key, label, bound, bad in (("min", "at least", param.min, lambda v, b: v < b),
                                           ("max", "at most", param.max, lambda v, b: v > b)):
                if bound is None:
                    continue
                try:
                    if is_expr(bound):
                        scope = scope or self.world.evaluation.scope(
                            actor=actor, viewer=viewer_for(f"ParamSpec.{key}", actor), params=params)
                        limit = compile_expr(bound)(scope)
                    else:
                        limit = bound
                except ExprError as exc:
                    raise RunError(str(exc), f"actions.{action}.params.{pname}.{key}") from None
                if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))):
                    raise RunError(f"the {label} bound must be a number, got {format_value(limit)}",
                                   f"actions.{action}.params.{pname}")
                if limit is not None and bad(value, limit):
                    stated = int(limit) if kind == "int" and float(limit).is_integer() else limit  # 10, not 10.0
                    return None, f"must be {label} {preview(stated)} (got {preview(value)})"
            if param.step is not None:
                base = compile_expr(param.min)(scope or self.world.evaluation.scope(
                    actor=actor, viewer=viewer_for("ParamSpec.min", actor), params=params)) \
                    if is_expr(param.min) else param.min
                offset = (value - (base or 0)) / param.step
                if abs(offset - round(offset)) > STEP_TOLERANCE:
                    return None, f"must go in steps of {preview(param.step)} from {preview(base or 0)} " \
                                 f"(got {preview(value)})"
            return value, None
        if kind == "file":
            return file_value(self.world, param, raw)
        if kind == "bool":
            if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
                return raw.strip().lower() == "true", None
            if not isinstance(raw, bool):
                return None, f"must be true or false, got {preview(raw)}"
            return raw, None
        if kind == "text":
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) \
                    or (isinstance(raw, float) and not math.isfinite(raw)) \
                    or (isinstance(raw, int) and abs(raw) > MAX_SAFE_INT):
                return None, f"must be text, got {preview(raw)}"
            limit = param.max_len if param.max_len is not None else TEXT_MAX_LEN
            if isinstance(raw, str) and len(raw) > limit:
                return None, f"is {len(raw)} characters; the limit is {limit}"
            return Untrusted(str.__str__(raw) if isinstance(raw, str) else repr(raw)), None
        if kind == "enum":
            values = self.enum_values(actor, action, pname, param, params)
            same = [v for v in values if v == raw and isinstance(v, bool) == isinstance(raw, bool)]
            if same:
                return same[0], None
            if isinstance(raw, str):
                folded = [v for v in values if isinstance(v, str) and v.lower() == raw.strip().lower()]
                if len(folded) == 1:
                    return folded[0], None
            return None, f"must be one of {', '.join(format_value(v) for v in values)} (got {preview(raw)})"
        if kind == "list":
            return self._list_value(actor, action, pname, param, raw, params)
        if kind == "entity":
            chosen = self._chosen(actor, param, raw, params)
            if chosen is not None:
                return chosen, None
            choices = self.book.choices(actor, action, pname, param, params)
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                raw = raw["id"]
            if not isinstance(raw, str):
                return None, f"must be an id, got {preview(raw)}"
            key = raw.strip()
            for choice in choices:
                if choice.id == key:
                    return choice, None
            by_name = [c for c in choices if (c.name or "").lower() == key.lower()]
            if len(by_name) == 1:
                return by_name[0], None
            listing = ", ".join(c.id for c in choices[:8]) + (" …" if len(choices) > 8 else "")
            shown = f"'{raw}'" if len(raw) <= 60 else preview(raw)
            return None, f"{shown} is not a valid {param.of} {_given(param, params)} (valid: {listing or 'none'})"
        raise RunError(f"unknown parameter type '{kind}'", f"actions.{action}.params.{pname}")

    def enum_values(self, actor: Entity, action: str, pname: str, param: ParamSpec,
                    params: dict[str, Any]) -> list[Any]:
        """The values an enum parameter allows, given the arguments before it."""
        values = param.values
        if isinstance(values, str):
            try:
                values = compile_expr(values)(self.world.evaluation.scope(
                    actor=actor, viewer=viewer_for("ParamSpec.values", actor), params=params))
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{action}.params.{pname}.values") from None
        if values is not None and not isinstance(values, (list, tuple)):
            raise RunError(f"values must give a list, got {format_value(values)}",
                           f"actions.{action}.params.{pname}.values")
        return [plain_value(v) for v in (values or [])]

    def _chosen(self, actor: Entity, param: ParamSpec, raw: Any, params: dict[str, Any]) -> Entity | None:
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
        try:  # it draws nothing: whether a call is allowed is decided without luck (see ActionBook.deciding)
            viewer = viewer_for("ParamSpec.where", actor)
            scope = self.world.evaluation.scope(actor=actor, viewer=viewer, params=params)
            holds = truthy(expr(scope.child(it=entity)))
        except ExprError:
            holds = False  # the full listing reports it
        return entity if holds else None

    def _list_value(self, actor: Entity, action: str, pname: str, param: ParamSpec,
                    raw: Any,
                    params: dict[str, Any]) -> tuple[Any, str | None]:
        if isinstance(raw, str):  # a model sometimes sends a list as JSON text or comma-separated words
            text = raw.strip()
            try:
                decoded = json.loads(text) if text.startswith("[") else None
            except ValueError:
                decoded = None
            raw = decoded if isinstance(decoded, list) else [part.strip() for part in text.split(",") if part.strip()]
        if not isinstance(raw, (list, tuple)):
            return None, f"must be a list, got {preview(raw)}"
        def count(bound: Any, key: str) -> int | None:
            path = f"actions.{action}.params.{pname}.{key}"
            try:
                viewer = viewer_for(f"ParamSpec.{key}", actor)
                value = compile_expr(bound)(self.world.evaluation.scope(actor=actor, viewer=viewer, params=params)) \
                    if is_expr(bound) else bound
            except ExprError as exc:
                raise RunError(str(exc), path) from None
            return item_count(value, path)

        low, high = list_bounds(param, count)
        if len(raw) < low:
            return None, f"needs at least {low} item(s), got {len(raw)}"
        if high is not None and len(raw) > high:
            return None, f"allows at most {high} item(s), got {len(raw)}"
        item = item_spec(param)
        values: list[Any] = []
        seen: set = set()
        for index, element in enumerate(raw):
            value, problem = self.value(actor, action, pname, item, element, params)
            if problem:
                return None, f"item {index + 1} {problem}"
            key = getattr(value, "id", None) or json.dumps(plain_value(value), sort_keys=True, default=str)
            if param.unique and key in seen:
                return None, f"lists {format_value(plain_value(value))} more than once"
            seen.add(key)
            values.append(value)
        return values, None


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


def _given(param: ParamSpec, params: dict[str, Any]) -> str:
    """Where an entity choice was refused: "here", or the earlier arguments its `where` reads ("given a=b1")."""
    read = sorted({path[1] for path in compile_expr(param.where).paths if path[0] == "params" and len(path) > 1}) \
        if param.where is not None else []
    if not read:
        return "here"
    shown = [f"{name}={format_value(getattr(params.get(name), 'id', params.get(name)))}" for name in read]
    return "given " + ", ".join(shown)


def _waiting_on(pname: str, failed: list[str]) -> str:
    return f"{pname} can be checked once {', '.join(failed)} {'is' if len(failed) == 1 else 'are'} corrected"
