"""Reading values and applying operators under expression semantics: strict types, finite numbers, size caps."""
from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable, Dict, Mapping, Optional

from ..entity import Entity as _Entity
from .base import MAX_INT_BITS, MAX_LIST_LEN, MAX_TEXT_LEN, ExprError, Untrusted, charge

__all__ = ["attr"]

_ENTITY_FIELDS = frozenset({"id", "name", "type", "alive", "at"})


def attr(obj: Any, name: str, source: Optional[str] = None) -> Any:
    """Read ``obj.name`` under expression semantics (entities, dicts, records)."""
    if name.startswith("_"):
        raise ExprError(f"private field '{name}' cannot be read", source)
    if type(obj) is _Entity:  # the common case, first
        own = obj.properties
        if name in own and name not in _ENTITY_FIELDS:
            return own[name]
    if obj is None:
        raise ExprError(f"cannot read '.{name}' of null", source)
    reader = getattr(obj, "expr_attr", None)
    if reader is not None:
        return reader(name, source)
    # Entities from the world store (fg_env.entity.Entity) — read by duck type so
    # this module stays independent of the storage layer.
    props = getattr(obj, "properties", None)
    if isinstance(props, dict) and hasattr(obj, "entity_type"):
        if name == "id":
            return obj.id
        if name == "name":
            return obj.name
        if name == "type":
            return obj.entity_type
        if name == "alive":
            return obj.alive
        if name == "at":
            return obj.location_id
        if name in props:
            return props[name]
        known = ", ".join(sorted(k for k in props if not k.startswith("_")))
        raise ExprError(f"{obj.entity_type} '{obj.id}' has no property '{name}' (has: {known})", source)
    if isinstance(obj, Mapping):
        if name in obj:
            return obj[name]
        if not obj:
            raise ExprError(f"no field '{name}': the map is empty (nothing has set it yet)", source)
        raise ExprError(f"no field '{name}' (fields: {', '.join(sorted(str(k) for k in obj))})", source)
    if isinstance(obj, (list, tuple)) and name in ("count", "size", "length"):
        return len(obj)
    raise ExprError(f"cannot read '.{name}' of {type(obj).__name__} {obj!r}", source)


def _index(container: Any, index: Any, source: str) -> Any:
    """``container[index]``: a list element, or a field of a map or entity."""
    if isinstance(container, (list, tuple)):
        if isinstance(index, bool) or not isinstance(index, int):
            raise ExprError(f"list index must be a whole number, got {_describe(index)}", source)
        if not -len(container) <= index < len(container):
            raise ExprError(f"index {index} is out of range (length {len(container)})", source)
        return container[index]
    if isinstance(container, Mapping) or hasattr(container, "entity_type"):
        return attr(container, str(index), source)
    raise ExprError(f"cannot index {_describe(container)}", source)


def _entity_id(value: Any) -> Any:
    if hasattr(value, "entity_type") and hasattr(value, "id"):
        return value.id
    return value


def _eq(a: Any, b: Any) -> bool:
    return _entity_id(a) == _entity_id(b)


def _number(value: Any, source: str, what: str = "a number") -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExprError(f"expected {what}, got {_describe(value)}", source)
    if isinstance(value, float) and not math.isfinite(value):
        raise ExprError(f"expected a finite number, got {value}", source)
    return value


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if hasattr(value, "entity_type"):
        return f"entity '{value.id}'"
    text = repr(value)
    return f"{type(value).__name__} {text[:40]}"


def _finite(value: Any, source: str) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ExprError("arithmetic produced a non-finite number", source)
    return value


def _in(item: Any, container: Any, source: str) -> bool:
    if isinstance(container, str):
        if not isinstance(item, str):
            raise ExprError(f"'in' text needs text on the left, got {_describe(item)}", source)
        return item in container
    if isinstance(container, Mapping):
        return item in container
    if isinstance(container, (list, tuple, set, frozenset)):
        charge(len(container), source)
        key = _entity_id(item)
        return any(_entity_id(x) == key for x in container)
    raise ExprError(f"'in' needs a list or text on the right, got {_describe(container)}", source)


def _add(a: Any, b: Any, source: str) -> Any:
    if isinstance(a, str) and isinstance(b, str):
        if len(a) + len(b) > MAX_TEXT_LEN:
            raise ExprError(f"text would be {len(a) + len(b):,} characters; the limit is {MAX_TEXT_LEN:,}", source)
        joined = str.__add__(a, b)
        return Untrusted(joined) if isinstance(a, Untrusted) or isinstance(b, Untrusted) else joined
    if isinstance(a, list) and isinstance(b, list):
        if len(a) + len(b) > MAX_LIST_LEN:
            raise ExprError(f"a list would have {len(a) + len(b):,} items; the limit is {MAX_LIST_LEN:,}", source)
        charge(len(a) + len(b), source)
        return a + b
    return _finite(_number(a, source) + _number(b, source), source)


def _too_big(bits: int, source: str) -> ExprError:
    return ExprError(f"a whole number of about {bits:,} bits is past the limit of {MAX_INT_BITS:,} bits", source)


def _mul(a: Any, b: Any, source: str) -> Any:
    a, b = _number(a, source), _number(b, source)
    if type(a) is float or type(b) is float:
        return _finite(a * b, source)
    bits = a.bit_length() + b.bit_length()
    if bits > MAX_INT_BITS + 1:
        raise _too_big(bits, source)
    return a * b


def _div(op: Callable[[Any, Any], Any]) -> Callable[[Any, Any, str], Any]:
    def run(a: Any, b: Any, source: str) -> Any:
        a, b = _number(a, source), _number(b, source)
        if b == 0:
            raise ExprError("division by zero", source)
        return _finite(op(a, b), source)

    return run


def _pow(a: Any, b: Any, source: str) -> Any:
    a, b = _number(a, source), _number(b, source)
    if abs(b) > 1024:
        raise ExprError("exponent too large", source)
    if isinstance(a, int) and isinstance(b, int) and b > 1 and abs(a) > 1:
        bits = (abs(a).bit_length() - 1) * b
        if bits > MAX_INT_BITS:
            raise _too_big(bits, source)
    try:
        return _finite(a ** b, source)
    except OverflowError:  # its own text varies by platform ("Result too large", "Numerical result out of range")
        raise ExprError("power failed: the result is too large", source) from None
    except ZeroDivisionError as exc:
        raise ExprError(f"power failed: {exc}", source) from None


def _arith(op: Callable[[Any, Any], Any]) -> Callable[[Any, Any, str], Any]:
    return lambda a, b, source: _finite(op(_number(a, source), _number(b, source)), source)


_BINARY: Dict[type, Callable[[Any, Any, str], Any]] = {
    ast.Add: _add,
    ast.Sub: _arith(operator.sub),
    ast.Mult: _mul,
    ast.Div: _div(operator.truediv),
    ast.FloorDiv: _div(operator.floordiv),
    ast.Mod: _div(operator.mod),
    ast.Pow: _pow,
}


def _ordered(op: Callable[[Any, Any], bool]) -> Callable[[Any, Any, str], bool]:
    def run(a: Any, b: Any, source: str) -> bool:
        numeric = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b))
        if not numeric and not (isinstance(a, str) and isinstance(b, str)):
            raise ExprError(f"cannot order {_describe(a)} and {_describe(b)}", source)
        return op(a, b)

    return run


_COMPARE: Dict[type, Callable[[Any, Any, str], bool]] = {
    ast.Eq: lambda a, b, s: _eq(a, b),
    ast.NotEq: lambda a, b, s: not _eq(a, b),
    ast.Lt: _ordered(operator.lt),
    ast.LtE: _ordered(operator.le),
    ast.Gt: _ordered(operator.gt),
    ast.GtE: _ordered(operator.ge),
    ast.In: lambda a, b, s: _in(a, b, s),
    ast.NotIn: lambda a, b, s: not _in(a, b, s),
}
