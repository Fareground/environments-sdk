"""Reading values and applying operators under expression semantics: strict types, finite numbers, size caps."""
from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping
from typing import Any

from ..world.entity import Entity as _Entity
from .base import MAX_INT_BITS, MAX_LIST_LEN, MAX_TEXT_LEN, ExprError, PrivateRead, Untrusted, WrongKind, charge

__all__ = ["attr", "EVERYONE", "map_key"]

_ENTITY_FIELDS = frozenset({"id", "name", "type", "alive", "at"})


def attr(obj: Any, name: str, source: str | None = None, scope: Any = None) -> Any:
    """Read ``obj.name`` under expression semantics (entities, dicts, records). With the ``scope`` it is read in, an
    agent's private property is refused while ``$viewer`` is bound to anyone but that agent."""
    if name.startswith("_"):
        raise ExprError(f"private field '{name}' cannot be read", source)
    if type(obj) is _Entity:  # the common case, first
        own = obj.properties
        if name in own and name not in _ENTITY_FIELDS:
            if scope is not None and name in scope.world.private_names:
                _check_visible(obj, name, scope, source)
            return own[name]
    if obj is None:
        raise ExprError(f"cannot read '.{name}' of null", source)
    reader = getattr(obj, "expr_attr", None)
    if reader is not None:
        return reader(name, source)
    # Entities from the world store (fg_env.world.entity.Entity) — read by duck type so
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
            if scope is not None and name in scope.world.private_metrics:
                _check_metric(obj, name, scope, source)
            return obj[name]
        if not obj:
            raise ExprError(f"no field '{name}': the map is empty (nothing has set it yet)", source)
        raise ExprError(f"no field '{name}' (fields: {', '.join(sorted(str(k) for k in obj))})", source)
    if isinstance(obj, (list, tuple)) and name in ("count", "size", "length"):
        return len(obj)
    if isinstance(obj, (list, tuple)):  # never the items themselves: they may be entities with private props
        items = f"{len(obj)} item" + ("" if len(obj) == 1 else "s")
        raise ExprError(f"cannot read '.{name}' of a list ({items}); pick one first, e.g. $first(list).{name}", source)
    raise ExprError(f"cannot read '.{name}' of {type(obj).__name__} {obj!r}", source)


class _Everyone:
    """The ``$viewer`` of text sent to more than one agent (an announcement, news): no agent's private property may
    show in it, not even the actor's."""

    name = "everyone"

    def __repr__(self) -> str:
        return "everyone"


EVERYONE = _Everyone()


def _check_visible(entity: _Entity, name: str, scope: Any, source: str | None) -> None:
    """Refuse (:class:`PrivateRead`) reading ``entity``'s private ``name`` in what one agent is shown or offered, or
    in text sent to several (:data:`EVERYONE`). Game logic binds no ``$viewer`` and reads the true state; an agent
    always sees its own properties."""
    viewer = scope.vars.get("viewer")
    if viewer is None:  # game logic reads the true state; note when it reads what the acting agent may not see
        actor = scope.vars.get("actor")
        if (actor is None or _entity_id(actor) != entity.id) and scope.world.is_hidden(entity.entity_type, name):
            scope.world.hidden_reads += 1
        return
    if _entity_id(viewer) == entity.id or not scope.world.is_private(entity.entity_type, name):
        return
    if viewer is EVERYONE:
        raise PrivateRead(
            f"{entity.name}'s {name} is private, and this text is sent to others than {entity.name}: work out what "
            "they may learn in game logic (e.g. `\"$shown = ...\"` in the action's do) and show that, or send it `to` "
            f"{entity.name} alone", source)
    raise PrivateRead(
        f"{entity.name}'s {name} is private, and this is what {getattr(viewer, 'name', viewer)} is shown or offered: "
        "read only the agent's own (guard with `$it.id == $actor.id`), or work out what it may learn in game logic "
        "(an action's do, an event) and show that", source)


def _check_metric(values: Mapping[str, Any], name: str, scope: Any, source: str | None) -> None:
    """Refuse (:class:`PrivateRead`) reading metric ``name`` (in ``$metrics`` or ``$series``), worked out from agents'
    private properties, in what an agent is shown or offered."""
    world, viewer = scope.world, scope.vars.get("viewer")
    if viewer is None or not (values is getattr(world, "metrics", None) or values is getattr(world, "series", None)):
        return
    raise PrivateRead(
        f"metric {name} is worked out from agents' private properties, and this is what "
        f"{getattr(viewer, 'name', viewer)} is shown or offered: show a metric that reads no private property, or "
        "work out what the agent may learn in game logic (an action's do, an event) and show that", source)


def _index(container: Any, index: Any, source: str, scope: Any = None) -> Any:
    """``container[index]``: a list element, or a field of a map or entity."""
    if isinstance(container, (list, tuple)):
        if isinstance(index, bool) or not isinstance(index, int):
            raise ExprError(f"list index must be a whole number, got {_describe(index)}", source)
        if not -len(container) <= index < len(container):
            raise ExprError(f"index {index} is out of range (length {len(container)})", source)
        return container[index]
    if isinstance(container, Mapping):
        return attr(container, str(map_key(index)), source, scope)
    if hasattr(container, "entity_type"):
        return attr(container, str(index), source, scope)
    raise ExprError(f"cannot index {_describe(container)}", source)


def _entity_id(value: Any) -> Any:
    kind = type(value)
    if kind is str or kind is int or kind is float or value is None:  # the common cases, without a failed lookup
        return value
    if kind is _Entity:
        return value.id
    if hasattr(value, "entity_type") and hasattr(value, "id"):
        return value.id
    return value


def map_key(value: Any) -> Any:
    """The key ``value`` names in a map. Map keys are text (as in JSON): a number names the key spelled like it
    (``{1: 3}`` holds the key ``'1'``), an entity names its id."""
    value = _entity_id(value)
    return str(value) if type(value) is int or type(value) is float else value


def _eq(a: Any, b: Any) -> bool:
    return _entity_id(a) == _entity_id(b)


def _number(value: Any, source: str, what: str = "a number") -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WrongKind(f"expected {what}, got {_describe(value)}", source)
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
    if isinstance(container, (list, tuple, set, frozenset)):  # before Mapping: an ABC check costs far more
        charge(len(container), source)
        key = _entity_id(item)
        return any(_entity_id(x) == key for x in container)
    if isinstance(container, Mapping):
        key = map_key(item)
        return isinstance(key, str) and key in container
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
        result = a ** b
    except OverflowError:  # its own text varies by platform ("Result too large", "Numerical result out of range")
        raise ExprError("power failed: the result is too large", source) from None
    except ZeroDivisionError as exc:
        raise ExprError(f"power failed: {exc}", source) from None
    if isinstance(result, complex):  # a negative number to a fractional power
        raise ExprError(f"{a} ** {b} has no real result", source)
    return _finite(result, source)


def _arith(op: Callable[[Any, Any], Any]) -> Callable[[Any, Any, str], Any]:
    return lambda a, b, source: _finite(op(_number(a, source), _number(b, source)), source)


_BINARY: dict[type, Callable[[Any, Any, str], Any]] = {
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


_COMPARE: dict[type, Callable[[Any, Any, str], bool]] = {
    ast.Eq: lambda a, b, s: _eq(a, b),
    ast.NotEq: lambda a, b, s: not _eq(a, b),
    ast.Lt: _ordered(operator.lt),
    ast.LtE: _ordered(operator.le),
    ast.Gt: _ordered(operator.gt),
    ast.GtE: _ordered(operator.ge),
    ast.In: lambda a, b, s: _in(a, b, s),
    ast.NotIn: lambda a, b, s: not _in(a, b, s),
}
