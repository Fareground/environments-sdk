"""Text templates: the plain-language lines agents read.

``"[{id}] {name} · {price|money} · {rating|1} stars"``

* ``{field}`` reads a field of the template's default subject (the listed item in a
  view, the actor in a self line, the entry in a record).
* ``{$expr}`` evaluates any expression: ``{$round + 1}``, ``{$count(buyer)}``.
* ``{value|format}`` formats: ``money``, ``pct``, ``int``, ``0``–``4`` (decimals),
  ``upper``, ``lower``, ``title``, ``yesno``, ``list``.
* ``{{`` and ``}}`` are literal braces.

Numbers render compactly (``3.5``, not ``3.500000``); entities render as their name;
lists join with commas; null renders as ``—``.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .expr import ExprError, Expr, Scope, Untrusted, compile_expr

__all__ = ["Template", "compile_template", "render", "format_value", "apply_format", "entity_handles",
           "quoted_placeholders"]

_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$")
#: A placeholder wrapped in «» by the template itself: participant text already renders inside «».
_REQUOTED = re.compile(r"«\s*(\{[^{}]*\})\s*»")
#: While an agent's reading renders: which entities show their [id] handle after their name.
_HANDLES: ContextVar[Optional[Callable[[Any], bool]]] = ContextVar("fg_env_entity_handles", default=None)


@contextmanager
def entity_handles(show: Optional[Callable[[Any], bool]]) -> Iterator[None]:
    """Render entities as ``Name [id]`` when ``show(entity)`` holds (None: names only) inside the block."""
    token = _HANDLES.set(show)
    try:
        yield
    finally:
        _HANDLES.reset(token)


def quoted_placeholders(source: str) -> List[str]:
    """The placeholders a template wraps in «» itself (``«{$it.text}»``)."""
    return _REQUOTED.findall(source) if "«" in source else []


def format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Untrusted):
        return "«" + str.__str__(value).replace("«", "‹").replace("»", "›") + "»"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if hasattr(value, "entity_type") and hasattr(value, "name"):
        name = str(value.name or value.id)
        show = _HANDLES.get()
        return f"{name} [{value.id}]" if show is not None and name != value.id and show(value) else name
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(v) for v in value)
    if isinstance(value, dict):
        # A key that is participant text renders quoted, like any other participant text.
        return ", ".join(f"{format_value(k) if isinstance(k, Untrusted) else k}: {format_value(v)}"
                         for k, v in value.items())
    return str(value)


def _money(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return format_value(value)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _decimals(n: int) -> Callable[[Any], str]:
    def run(value: Any) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return format_value(value)
        return f"{value:,.{n}f}"

    return run


_FORMATS: Dict[str, Callable[[Any], str]] = {
    "money": _money,
    "pct": lambda v: f"{v * 100:.0f}%" if isinstance(v, (int, float)) and not isinstance(v, bool) else format_value(v),
    "pct1": lambda v: f"{v * 100:.1f}%" if isinstance(v, (int, float)) and not isinstance(v, bool) else format_value(v),
    "int": lambda v: f"{round(v):,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else format_value(v),
    "upper": lambda v: format_value(v).upper(),
    "lower": lambda v: format_value(v).lower(),
    "title": lambda v: format_value(v).title(),
    "yesno": lambda v: "yes" if v else "no",
    "list": lambda v: format_value(v if isinstance(v, (list, tuple)) else [v]),
    **{str(n): _decimals(n) for n in range(5)},
}

FORMATS = tuple(_FORMATS)


def apply_format(value: Any, fmt: str) -> str:
    """``value`` shown with one of :data:`FORMATS`, as ``{value|fmt}`` renders it (plainly when it cannot be)."""
    try:
        return _FORMATS[fmt](value)
    except (KeyError, ArithmeticError, ValueError, TypeError):
        return format_value(value)


@dataclass(frozen=True)
class Template:
    source: str
    parts: Tuple[Any, ...]
    expressions: Tuple[Expr, ...]

    def render(self, scope: Scope) -> str:
        out: List[str] = []
        for part in self.parts:
            if isinstance(part, str):
                out.append(part)
                continue
            expr, fmt = part
            try:
                value = expr(scope)
            except ExprError as exc:
                raise ExprError(f"template {self.source!r}: {exc.detail}", exc.source) from None
            try:
                out.append(_FORMATS[fmt](value) if fmt else format_value(value))
            except (ArithmeticError, ValueError, TypeError, RecursionError) as exc:
                raise ExprError(f"template {self.source!r}: cannot format {type(value).__name__} value ({exc})",
                                expr.source) from None
        return "".join(out)


@lru_cache(maxsize=4_096)
def compile_template(source: str, subject: Optional[str] = "it") -> Template:
    """Compile a template. Bare ``{field}`` reads ``$<subject>.field``."""
    if not isinstance(source, str):
        raise ExprError("a template must be text", str(source))
    stripped = source.strip()
    if "{" not in stripped and stripped.startswith("$") and len(stripped) > 1 and (stripped[1].isalpha() or stripped[1] == "_"):
        source = "{" + stripped + "}"  # a text field holding only an expression renders its value
    parts: List[Any] = []
    exprs: List[Expr] = []
    buf: List[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if source.startswith("{{", i) or source.startswith("}}", i):
            buf.append(ch)
            i += 2
            continue
        if ch == "}":
            raise ExprError("unmatched '}' (write '}}' for a literal brace)", source)
        if ch != "{":
            buf.append(ch)
            i += 1
            continue
        end, depth, quote = i + 1, 1, None
        while end < n and depth:
            c = source[end]
            if quote:
                if c == "\\":
                    end += 1  # an escaped character never closes the quote: {'it\\'s'}
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            end += 1
        if depth:
            raise ExprError("unclosed '{' in template: close it with '}', or write '{{' for a literal brace", source)
        inner = source[i + 1:end - 1].strip()
        fmt = None
        if "|" in inner and not inner.rsplit("|", 1)[1].strip().startswith("|"):
            head, tail = inner.rsplit("|", 1)
            if tail.strip() in _FORMATS:
                inner, fmt = head.strip(), tail.strip()
            elif "||" not in inner:
                raise ExprError(f"unknown format '{tail.strip()}' (use one of: {', '.join(FORMATS)})", source)
        if not inner:
            raise ExprError("empty {} in template", source)
        if inner.startswith("$") and len(inner) > 1 and not (inner[1].isalpha() or inner[1] == "_"):
            inner = inner[1:].strip()  # `{$'yes' if $x else 'no'}` → the expression after the marker
        if "$" not in inner and not inner.startswith(("'", '"', "(")):
            if not _FIELD.match(inner):
                raise ExprError(f"'{{{inner}}}' is not a field name; write an expression as {{$...}}", source)
            if subject is None:
                raise ExprError(f"'{{{inner}}}' needs a subject; write {{$actor.{inner}}} or similar", source)
            inner = f"${subject}.{inner}"
        expr = compile_expr(inner)
        exprs.append(expr)
        if buf:
            parts.append("".join(buf))
            buf = []
        parts.append((expr, fmt))
        i = end
    if buf:
        parts.append("".join(buf))
    return Template(source, tuple(parts), tuple(exprs))


def render(source: str, scope: Scope, subject: Optional[str] = "it") -> str:
    return compile_template(source, subject).render(scope)
