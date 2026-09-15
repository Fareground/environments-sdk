"""The fg_env expression language — one small, safe language for every contract field.

Conditions, effects, views, metrics, outputs and physics bindings all use it::

    $actor.cash >= $params.offer.price * $params.qty
    $count(buyer, $it.cash > 0)
    $sum(offer, $it.sold * $it.price)
    $top(offer, $it.rating, 5)
    $params.qty if $actor.vip else 1

Rules:

* ``$name`` is a root supplied by the context (``$actor``, ``$params``, ``$it``,
  ``$inputs``, ``$world``, ``$round`` …); ``$name(...)`` calls a built-in function.
* Bare words are symbols (plain text): ``$actor.status == open``, ``$count(buyer)``.
  ``true``, ``false`` and ``null`` are literals. Quote text that contains spaces.
* Python operator syntax: ``+ - * / // % **``, comparisons, ``and or not``
  (``&& || !`` also accepted), ``x if cond else y``, ``in``, lists ``[1, 2]``.
* Entities expose ``id``, ``name``, ``type``, ``alive``, ``at`` and their properties.
* Evaluation is strict: an unknown property, a missing root or a type error raises
  :class:`ExprError` naming the expression and what to fix — a broken rule never
  silently evaluates to zero.

The parser uses Python's ``ast`` module purely as a grammar. Nothing is executed
as Python; only whitelisted node types are interpreted.
"""
from __future__ import annotations

import ast
import math
import operator
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "EVAL_BUDGET",
    "MAX_INT_BITS",
    "MAX_LIST_LEN",
    "MAX_RANGE",
    "MAX_TEXT_LEN",
    "Untrusted",
    "tainted",
    "derived",
    "charge",
    "check_size",
    "shared_budget",
    "ExprError",
    "Expr",
    "Scope",
    "World",
    "FunctionSpec",
    "FUNCTIONS",
    "compile_expr",
    "evaluate",
    "resolve",
    "is_expr",
    "truthy",
    "attr",
    "function",
]

_MAX_SOURCE = 8_192
_MAX_NODES = 2_048
_EXPR_MARK = re.compile(r"\$[A-Za-z_]")
_ROOT_PREFIX = "__r_"
_FUNC_PREFIX = "__f_"

#: Work one top-level evaluation may do: items visited by per-item arguments, collection
#: elements read or built, nested evaluations. Guards against runaway nesting like
#: ``$map($range(100000), $map($range(100000), ...))``.
EVAL_BUDGET = 2_000_000
#: Longest list ``$range`` may produce.
MAX_RANGE = 100_000
#: Longest list any operation may build.
MAX_LIST_LEN = 1_000_000
#: Longest text any operation may build.
MAX_TEXT_LEN = 1_000_000
#: Largest whole number (in bits) ``*`` and ``**`` may produce.
MAX_INT_BITS = 4_096


class Untrusted(str):
    """Text written by a participant. It keeps that provenance wherever it is stored and
    renders wrapped in «» so other agents read it as information, never instructions.

    ``str(value)`` keeps the marker, so code that normalises keys or values with ``str()``
    cannot silently launder participant text; ``str.__str__(value)`` gives the plain text."""

    __slots__ = ()

    def __str__(self) -> str:
        return self


def tainted(value: Any) -> bool:
    """True when ``value`` is, or contains (in list items, map keys or values), participant text."""
    if isinstance(value, Untrusted):
        return True
    if isinstance(value, (str, int, float, bool)) or value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(tainted(item) for item in value)
    if isinstance(value, Mapping):
        return any(tainted(key) or tainted(item) for key, item in value.items())
    return False


def derived(text: str, *sources: Any) -> str:
    """``text`` marked untrusted when any of the values it was derived from carries participant text."""
    return Untrusted(text) if any(tainted(source) for source in sources) else text


class ExprError(ValueError):
    """An expression is malformed or cannot be evaluated against the current state."""

    def __init__(self, message: str, source: Optional[str] = None):
        self.source = source
        self.detail = message
        super().__init__(f"{message} — in `{source}`" if source else message)


# ---------------------------------------------------------------------------
# Execution budget
# ---------------------------------------------------------------------------


class _Budget(threading.local):
    """Per-thread work counter. Turns run on worker threads, so each keeps its own.

    ``hold`` counts open nesting points — a def call, a record-visibility rule, a shared
    block: while it is non-zero an evaluation is nested inside other work and charges that
    work's budget; at zero an evaluation is top-level and starts a fresh budget. Only those
    points run evaluations inside evaluations, so plain rules pay one read and one write."""

    hold = 0
    used = 0
    limit = EVAL_BUDGET
    #: The running expression's own ceiling: inside a shared block each expression still gets at most
    #: EVAL_BUDGET steps, so one hostile rule cannot spend the whole block's budget.
    cap = EVAL_BUDGET
    label = ""
    shared = False


_BUDGET = _Budget()


def charge(amount: int, source: Optional[str] = None) -> None:
    """Count ``amount`` units of work against the running evaluation's budget."""
    budget = _BUDGET
    budget.used += amount
    if budget.used > budget.cap and budget.cap < budget.limit:
        raise ExprError(
            f"evaluation exceeded its work budget of {EVAL_BUDGET:,} steps (items visited and elements built); "
            "narrow what it loops over or split the work across rounds", source)
    if budget.used > budget.limit:
        who = f"{budget.label} exceeded its shared" if budget.shared and budget.label else "evaluation exceeded its"
        raise ExprError(
            f"{who} work budget of {budget.limit:,} steps (items visited and elements built); "
            "narrow what it loops over or split the work across rounds", source)


def check_size(value: Any, source: Optional[str]) -> Any:
    """Refuse lists and text longer than :data:`MAX_LIST_LEN` / :data:`MAX_TEXT_LEN`."""
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LEN:
            raise ExprError(f"text would be {len(value):,} characters; the limit is {MAX_TEXT_LEN:,}", source)
    elif isinstance(value, (list, tuple)):
        if len(value) > MAX_LIST_LEN:
            raise ExprError(f"a list would have {len(value):,} items; the limit is {MAX_LIST_LEN:,}", source)
        charge(len(value), source)
    return value


@contextmanager
def shared_budget(limit: int = EVAL_BUDGET, label: str = "") -> Iterator[None]:
    """Make every evaluation inside the block share one budget of ``limit`` steps.

    Code that loops over expressions outside the language (an action's effects, a view's
    items) wraps the loop so the loop as a whole is bounded, not only each evaluation.
    Nested blocks keep the outermost budget."""
    budget = _BUDGET
    if budget.hold:
        yield
        return
    budget.hold, budget.shared, budget.used, budget.limit, budget.label = 1, True, 0, limit, label
    budget.cap = limit
    try:
        yield
    finally:
        budget.hold, budget.shared, budget.used, budget.limit, budget.label = 0, False, 0, EVAL_BUDGET, ""
        budget.cap = EVAL_BUDGET


def nested_free() -> bool:
    """True when no work budget is being shared, so each evaluation starts afresh and skipping one is unseen."""
    return not _BUDGET.hold


def _held(run: Callable[[], Any]) -> Any:
    """Run a function call's work as nested work (its evaluations charge the caller's budget)."""
    budget = _BUDGET
    budget.hold += 1
    try:
        return run()
    finally:
        budget.hold -= 1


def is_expr(value: Any) -> bool:
    """True when ``value`` is a string holding a ``$`` reference or function call."""
    return isinstance(value, str) and bool(_EXPR_MARK.search(value))


def truthy(value: Any) -> bool:
    """Truthiness used by conditions: ``None``, ``0``, ``""`` and empty lists are false."""
    return bool(value)


# ---------------------------------------------------------------------------
# World and scope
# ---------------------------------------------------------------------------


class World:
    """What expressions may read from the running environment.

    The runtime supplies a subclass bound to the live world. The base class is an
    empty world, which is what static checks and pure evaluations use.
    """

    rng: Any = None

    def entities_of(self, type_name: str) -> List[Any]:
        raise ExprError(f"no entities of type '{type_name}' exist in this context")

    def alive_of(self, type_name: str) -> Sequence[Any]:
        """The living entities of a type, possibly as a shared list that callers only read."""
        return self.entities_of(type_name)

    def entity(self, entity_id: str) -> Any:
        return None

    def records(self, name: str) -> List[Any]:
        raise ExprError(f"no record '{name}' exists in this context")

    def visible_records(self, name: str, viewer: Any) -> List[Any]:
        return self.records(name)

    def events(self, name: Optional[str], viewer: Any = None) -> List[Any]:
        return []

    def relation(self, a: Any, b: Any, kind: str) -> Optional[float]:
        return None

    def neighbors(self, entity: Any, kind: str) -> List[Any]:
        return []

    def link_view(self, a: Any, b: Any, kind: str) -> Any:
        return None

    def links_of(self, entity: Any, kind: str) -> List[Any]:
        return []

    def distance(self, a: Any, b: Any) -> float:
        raise ExprError("this environment declares no space")

    def is_type(self, name: str) -> bool:
        return False

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return type_name == ancestor

    def subtypes_of(self, type_name: str) -> Any:
        """``type_name`` and every type that extends it."""
        return {type_name}

    def has_def(self, name: str) -> bool:
        return False

    def defines(self, name: str) -> bool:
        """Whether the contract declares a def called ``name`` (with or without arguments)."""
        return False

    def call_def(self, name: str, args: List[Any], source: str) -> Any:
        """Call a contract-defined function (``defs``). The empty world has none."""
        from difflib import get_close_matches

        hint = get_close_matches(name, list(FUNCTIONS), n=1)
        raise ExprError(f"unknown function ${name}" + (f" — did you mean ${hint[0]}?" if hint else ""), source)


_EMPTY_WORLD = World()


class _Layer(Mapping):
    """Roots of a child scope: a few new names over the parent's roots, without copying them."""

    __slots__ = ("own", "parent")

    def __init__(self, own: Dict[str, Any], parent: Mapping[str, Any]):
        self.own = own
        self.parent = parent

    def __getitem__(self, name: str) -> Any:
        if name in self.own:
            return self.own[name]
        return self.parent[name]

    def __contains__(self, name: object) -> bool:
        return name in self.own or name in self.parent

    def __iter__(self) -> Any:
        seen = set(self.own)
        yield from self.own
        yield from (name for name in self.parent if name not in seen)

    def __len__(self) -> int:
        return len(set(self.own) | set(self.parent))


@dataclass(frozen=True)
class Scope:
    """Values visible to an expression: named roots plus the world to query."""

    vars: Mapping[str, Any] = field(default_factory=dict)
    world: World = _EMPTY_WORLD

    def child(self, **values: Any) -> "Scope":
        return Scope(_Layer(values, self.vars), self.world)

    def root(self, name: str, source: str) -> Any:
        try:
            return self.vars[name]
        except KeyError:
            if self.world.has_def(name):  # a def without arguments reads like a value: $negotiating
                return _held(lambda: self.world.call_def(name, [], source))
            available = ", ".join(f"${k}" for k in sorted(self.vars)) or "none"
            raise ExprError(f"${name} is not available here (available: {available})", source) from None


# ---------------------------------------------------------------------------
# Value access
# ---------------------------------------------------------------------------


def attr(obj: Any, name: str, source: Optional[str] = None) -> Any:
    """Read ``obj.name`` under expression semantics (entities, dicts, records)."""
    if name.startswith("_"):
        raise ExprError(f"private field '{name}' cannot be read", source)
    if type(obj) is _Entity:  # the common case, first
        props = obj.properties
        if name in props and name not in _ENTITY_FIELDS:
            return props[name]
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
        known = ", ".join(sorted(str(k) for k in obj))
        raise ExprError(f"no field '{name}' (fields: {known})", source)
    if isinstance(obj, (list, tuple)) and name in ("count", "size", "length"):
        return len(obj)
    raise ExprError(f"cannot read '.{name}' of {type(obj).__name__} {obj!r}", source)


from ..entity import Entity as _Entity  # noqa: E402 — storage type, read on the hot path

_ENTITY_FIELDS = frozenset({"id", "name", "type", "alive", "at"})


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
    except (OverflowError, ZeroDivisionError) as exc:
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

_LITERAL_NAMES = {"true": True, "false": False, "null": None}


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


Evaluator = Callable[[Scope], Any]

#: An :class:`EqualityGuard` key that could not be worked out up front.
_NO_KEY = object()


@dataclass(frozen=True)
class EqualityGuard:
    """``$it.field == value`` opening a condition (alone or as the first of an ``and``), where ``value``
    reads no item (no ``$it`` or ``$i``) and calls no function. That value is the same for
    every item and draws nothing, so an entity whose field differs makes the whole condition false: it
    can be skipped without evaluating the condition, with the same result, errors and random draws."""

    field: str
    value: Evaluator
    roots: FrozenSet[str]

    def key(self, scope: Scope) -> Any:
        """The value every item is compared with, or ``_NO_KEY`` when it cannot be known up front."""
        if not all(root in scope.vars for root in self.roots):
            return _NO_KEY  # a missing root may be a def, which is evaluated per item
        try:
            return _entity_id(self.value(scope))
        except Exception:  # evaluating per item raises the same way; nothing is skipped
            return _NO_KEY

    def rules_out(self, item: Any, key: Any) -> bool:
        """True when ``item`` is an entity whose field is certainly not ``key``."""
        if type(item) is not _Entity:
            return False
        field = self.field
        if field in _ENTITY_FIELDS:
            value = item.location_id if field == "at" else item.entity_type if field == "type" else getattr(item, field)
        elif field in item.properties:
            value = item.properties[field]
        else:
            return False  # evaluating it reports the missing property
        return bool(_entity_id(value) != key)


class Call:
    """Arguments of one function call: evaluate eagerly or per item (lazily)."""

    __slots__ = ("name", "nodes", "scope", "source")

    def __init__(self, name: str, nodes: Sequence[Evaluator], scope: Scope, source: str):
        self.name = name
        self.nodes = nodes
        self.scope = scope
        self.source = source

    def __len__(self) -> int:
        return len(self.nodes)

    def arg(self, index: int, default: Any = None) -> Any:
        if index >= len(self.nodes):
            return default
        return self.nodes[index](self.scope)

    def each(self, index: int, item: Any, position: int = 0) -> Any:
        """Evaluate argument ``index`` with ``$it`` bound to ``item`` (the enclosing ``$it`` is ``$outer``)."""
        # Not charged: per-item arguments run over a collection whose length was charged.
        return self.nodes[index](self.scope.child(it=item, i=position, outer=self.scope.vars.get("it")))

    def collection(self, index: int = 0) -> List[Any]:
        return self._items(self.arg(index), copy=True)  # type: ignore[return-value]

    def members(self, index: int = 0) -> Sequence[Any]:
        """Like :meth:`collection`, but a type's living entities come as the world's shared list: read only."""
        return self._items(self.arg(index), copy=False)

    def _items(self, value: Any, copy: bool) -> Sequence[Any]:
        items: Sequence[Any]
        if isinstance(value, str):
            if not self.scope.world.is_type(value):
                raise ExprError(
                    f"${self.name}: '{value}' is not an entity type (pass a type name or a list)",
                    self.source,
                )
            items = list(self.scope.world.alive_of(value)) if copy else self.scope.world.alive_of(value)
        elif value is None:
            return []
        elif isinstance(value, Mapping):
            items = list(value.values())
        elif isinstance(value, (list, tuple)):
            items = list(value)
        elif hasattr(value, "entity_type"):
            return [value]
        else:
            raise ExprError(f"${self.name}: expected an entity type or a list, got {_describe(value)}", self.source)
        budget = _BUDGET  # inlined charge(len(items)): every collection function passes here
        budget.used += len(items)
        if budget.used > budget.limit:
            charge(0, self.source)
        return items

    def filtered(self, index: int = 0, where: Optional[int] = None) -> List[Any]:
        items = self.collection(index)
        if where is None or where >= len(self.nodes):
            return items
        return [item for pos, item in self.candidates(items, where) if truthy(self.each(where, item, pos))]

    def candidates(self, items: Sequence[Any], where: int) -> Iterator[Tuple[int, Any]]:
        """``(position, item)`` for the items argument ``where`` may hold for. Items it cannot hold for are
        left out only when that is certain without evaluating it (see :class:`EqualityGuard`)."""
        guard: Optional[EqualityGuard] = getattr(self.nodes[where], "guard", None)
        key = guard.key(self.scope.child(outer=self.scope.vars.get("it"))) if guard is not None else _NO_KEY
        if key is _NO_KEY:
            return enumerate(items)
        assert guard is not None
        return ((pos, item) for pos, item in enumerate(items) if not guard.rules_out(item, key))

    def number(self, index: int, default: Any = None) -> Any:
        value = self.arg(index, default)
        return _number(value, self.source, f"a number for argument {index + 1} of ${self.name}")

    @property
    def rng(self) -> Any:
        rng = self.scope.world.rng
        if rng is None:
            raise ExprError(f"${self.name} needs randomness, which is not available here", self.source)
        return rng


@dataclass(frozen=True)
class FunctionSpec:
    """A built-in function available as ``$name(...)`` in every expression."""

    name: str
    impl: Callable[[Call], Any]
    signature: str
    doc: str
    min_args: int = 0
    max_args: Optional[int] = None
    lazy: FrozenSet[int] = frozenset()


FUNCTIONS: Dict[str, FunctionSpec] = {}


def function(
    signature: str,
    doc: str,
    *,
    min_args: int = 0,
    max_args: Optional[int] = None,
    lazy: Sequence[int] = (),
) -> Callable[[Callable[[Call], Any]], Callable[[Call], Any]]:
    """Register a built-in expression function. ``signature`` starts with its name."""

    def register(impl: Callable[[Call], Any]) -> Callable[[Call], Any]:
        name = signature.split("(", 1)[0]
        if name in FUNCTIONS:
            raise ValueError(f"built-in function ${name} is registered twice")
        FUNCTIONS[name] = FunctionSpec(name, impl, signature, doc, min_args, max_args, frozenset(lazy))
        return impl

    return register


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

_ALLOWED = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Attribute, ast.Subscript,
    ast.Call, ast.List, ast.Tuple, ast.Dict, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.In, ast.NotIn,
)


def _preprocess(source: str) -> str:
    """Map ``$root`` → ``__r_root``, ``$fn(`` → ``__f_fn(`` and C-style boolean operators."""
    out: List[str] = []
    i, n, quote = 0, len(source), None
    while i < n:
        ch = source[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            name = source[i + 1:j]
            if not name and i + 1 < n and source[i + 1] in "\"'(":
                i += 1  # `$'text'` and `$(a + b)` mean just the text or the group
                continue
            if not name or name[0].isdigit():
                raise ExprError("'$' must be followed by a name, like $actor or $count(...)", source)
            k = j
            while k < n and source[k] == " ":
                k += 1
            out.append((_FUNC_PREFIX if k < n and source[k] == "(" else _ROOT_PREFIX) + name)
            i = j
            continue
        if source.startswith("&&", i):
            out.append(" and ")
            i += 2
            continue
        if source.startswith("||", i):
            out.append(" or ")
            i += 2
            continue
        if ch == "!" and not source.startswith("!=", i):
            out.append(" not ")
            i += 1
            continue
        out.append(ch)
        i += 1
    if quote:
        raise ExprError("unclosed quote", source)
    return "".join(out)


@dataclass(frozen=True)
class Expr:
    """A compiled expression: evaluate it many times against different scopes."""

    source: str
    run: Evaluator
    roots: FrozenSet[str]
    functions: FrozenSet[str]
    symbols: FrozenSet[str]
    #: ``(root, field, field, ...)`` chains read from roots, e.g. ``("actor", "cash")``.
    paths: FrozenSet[Tuple[str, ...]] = frozenset()
    #: ``(function, first_argument_symbol)`` pairs, e.g. ``("count", "buyer")``.
    calls: FrozenSet[Tuple[str, Optional[str]]] = frozenset()
    #: ``(function, first_argument_symbol, ("it", field, ...))`` — item fields read inside
    #: per-item arguments, e.g. ``("sum", "offer", ("it", "price"))``.
    item_paths: FrozenSet[Tuple[str, Optional[str], Tuple[str, ...]]] = frozenset()
    #: ``(("actor", "status"), "open")`` — a root field compared with a bare word.
    comparisons: FrozenSet[Tuple[Tuple[str, ...], str]] = frozenset()
    #: ``(function, first_argument_symbol, ("it", field), word)`` — the same inside per-item arguments.
    item_comparisons: FrozenSet[Tuple[str, Optional[str], Tuple[str, ...], str]] = frozenset()
    #: ``(function, signature)`` — built-in calls with the wrong number of arguments. Valid when the
    #: contract defines its own function of that name (a def shadows a built-in); reported otherwise.
    arity_errors: FrozenSet[Tuple[str, str]] = frozenset()

    def __call__(self, scope: Scope) -> Any:
        budget = _BUDGET
        if budget.hold:  # nested inside other work (a def, a record rule, a shared block): charge it
            if budget.shared and budget.hold == 1:  # an expression directly in a shared block: its own ceiling too
                budget.cap = min(budget.limit, budget.used + EVAL_BUDGET)
            budget.used += 1
            if budget.used > budget.limit or budget.used > budget.cap:
                charge(0, self.source)
        else:  # a top-level evaluation starts a fresh budget
            budget.used = 0
            budget.cap = EVAL_BUDGET
        try:
            return self.run(scope)
        except ExprError:
            raise
        except RecursionError:
            raise ExprError("evaluation nested too deeply", self.source) from None
        except (ArithmeticError, IndexError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ExprError(f"could not evaluate: {type(exc).__name__}: {str(exc)[:200]}", self.source) from None

    def rules_out(self, scope: Scope) -> Optional[Callable[[Any], bool]]:
        """For evaluating this condition once per item (as ``$it``, each a top-level evaluation) over
        ``scope``: a test that is true for items it certainly does not hold for, so they need not be
        evaluated (see :class:`EqualityGuard`). None when no item can be ruled out that way."""
        guard: Optional[EqualityGuard] = getattr(self.run, "guard", None)
        if guard is None or not nested_free():  # nested evaluations charge a budget: evaluate every one
            return None
        key = guard.key(scope)
        if key is _NO_KEY:
            return None
        return lambda item: guard.rules_out(item, key)


@lru_cache(maxsize=16_384)
def compile_expr(source: str) -> Expr:
    """Parse and validate ``source`` once. Raises :class:`ExprError` on bad syntax."""
    if not isinstance(source, str) or not source.strip():
        raise ExprError("expression is empty", str(source))
    source = source.strip()
    if len(source) > _MAX_SOURCE:
        raise ExprError("expression is too long", source[:60] + "…")
    try:
        tree = ast.parse(_preprocess(source), mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"syntax error: {exc.msg}", source) from None
    except (RecursionError, MemoryError):
        raise ExprError("expression is nested too deeply", source) from None
    except ValueError as exc:  # e.g. a NUL character
        raise ExprError(f"syntax error: {exc}", source) from None
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_NODES:
        raise ExprError("expression is too large", source)
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"unsupported syntax ({type(node).__name__})", source)
        if isinstance(node, ast.Call) and (node.keywords or not (
            isinstance(node.func, ast.Name) and node.func.id.startswith(_FUNC_PREFIX)
        )):
            raise ExprError("only $functions can be called, with positional arguments", source)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExprError(f"private field '{node.attr}' cannot be read", source)
    compiler = _Compiler(source)
    try:
        run = compiler.node(tree.body)
    except RecursionError:
        raise ExprError("expression is nested too deeply", source) from None
    return Expr(source, run, frozenset(compiler.roots), frozenset(compiler.functions),
                frozenset(compiler.symbols), frozenset(compiler.paths), frozenset(compiler.calls),
                frozenset(compiler.item_paths), frozenset(compiler.comparisons),
                frozenset(compiler.item_comparisons), frozenset(compiler.arity_errors))


def _chain(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """``$a.b.c`` → ``("a", "b", "c")``; None when the chain does not start at a root."""
    fields: List[str] = []
    while isinstance(node, ast.Attribute):
        fields.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id.startswith(_ROOT_PREFIX):
        return (node.id[len(_ROOT_PREFIX):], *reversed(fields))
    return None


class _Compiler:
    def __init__(self, source: str):
        self.source = source
        self.roots: set = set()
        self.functions: set = set()
        self.symbols: set = set()
        self.paths: set = set()
        self.calls: set = set()
        self.arity_errors: set = set()
        self.item_paths: set = set()
        self.comparisons: set = set()
        self.item_comparisons: set = set()

    def node(self, node: ast.AST) -> Evaluator:
        method = getattr(self, "_" + type(node).__name__)
        return method(node)

    def _Constant(self, node: ast.Constant) -> Evaluator:
        value = node.value
        if not isinstance(value, (int, float, str, bool)) and value is not None:
            raise ExprError("unsupported literal", self.source)
        return lambda scope: value

    def _Name(self, node: ast.Name) -> Evaluator:
        name, source = node.id, self.source
        if name.startswith(_ROOT_PREFIX):
            root = name[len(_ROOT_PREFIX):]
            self.roots.add(root)
            return lambda scope: scope.root(root, source)
        if name.startswith(_FUNC_PREFIX):
            raise ExprError(f"${name[len(_FUNC_PREFIX):]} is a function; call it with (...)", source)
        if name in _LITERAL_NAMES:
            literal = _LITERAL_NAMES[name]
            return lambda scope: literal
        self.symbols.add(name)
        return lambda scope: name

    def _Attribute(self, node: ast.Attribute) -> Evaluator:
        chain = _chain(node)
        if chain is not None:
            self.paths.add(chain)
        base, name, source = self.node(node.value), node.attr, self.source
        return lambda scope: attr(base(scope), name, source)

    def _Subscript(self, node: ast.Subscript) -> Evaluator:
        base, key, source = self.node(node.value), self.node(node.slice), self.source

        def run(scope: Scope) -> Any:
            container, index = base(scope), key(scope)
            if isinstance(container, (list, tuple)):
                if isinstance(index, bool) or not isinstance(index, int):
                    raise ExprError(f"list index must be a whole number, got {_describe(index)}", source)
                if not -len(container) <= index < len(container):
                    raise ExprError(f"index {index} is out of range (length {len(container)})", source)
                return container[index]
            if isinstance(container, Mapping) or hasattr(container, "entity_type"):
                return attr(container, str(index), source)
            raise ExprError(f"cannot index {_describe(container)}", source)

        return run

    def _List(self, node: ast.List) -> Evaluator:
        items = [self.node(item) for item in node.elts]
        return lambda scope: [item(scope) for item in items]

    _Tuple = _List

    def _Dict(self, node: ast.Dict) -> Evaluator:
        keys: List[str] = []
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, (str, int, float)) and not isinstance(key.value, bool):
                keys.append(str(key.value))
            elif isinstance(key, ast.Name) and not key.id.startswith("__"):
                keys.append(key.id)
            else:
                raise ExprError("map keys must be text or numbers, like {wage: 3, 'job years': 2}", self.source)
        values = [self.node(value) for value in node.values]
        return lambda scope: {k: v(scope) for k, v in zip(keys, values)}

    def _BinOp(self, node: ast.BinOp) -> Evaluator:
        left, right, source = self.node(node.left), self.node(node.right), self.source
        op = _BINARY[type(node.op)]
        return lambda scope: op(left(scope), right(scope), source)

    def _UnaryOp(self, node: ast.UnaryOp) -> Evaluator:
        operand, source = self.node(node.operand), self.source
        if isinstance(node.op, ast.Not):
            return lambda scope: not truthy(operand(scope))
        sign = -1 if isinstance(node.op, ast.USub) else 1
        return lambda scope: sign * _number(operand(scope), source)

    def _BoolOp(self, node: ast.BoolOp) -> Evaluator:
        values = [self.node(v) for v in node.values]
        # Like Python: `a or b` gives the first truthy value (a default), `a and b` the first falsy one.
        if isinstance(node.op, ast.And):
            def run_and(scope: Scope) -> Any:
                result: Any = True
                for value in values:
                    result = value(scope)
                    if not truthy(result):
                        return result
                return result

            guard = self._guard(node.values[0])
            if guard is not None:
                setattr(run_and, "guard", guard)
            return run_and

        def run_or(scope: Scope) -> Any:
            result: Any = False
            for value in values:
                result = value(scope)
                if truthy(result):
                    return result
            return result

        return run_or

    def _word(self, node: ast.AST) -> List[str]:
        if isinstance(node, ast.Name) and not node.id.startswith("__") and node.id not in _LITERAL_NAMES:
            return [node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [w for item in node.elts for w in self._word(item)]
        return []

    def _Compare(self, node: ast.Compare) -> Evaluator:
        operands = [node.left, *node.comparators]
        for a, b in zip(operands, operands[1:]):
            for chain_node, other in ((a, b), (b, a)):
                chain = _chain(chain_node)
                if chain is not None and len(chain) > 1:
                    for word in self._word(other):
                        self.comparisons.add((chain, word))
        left = self.node(node.left)
        pairs = [(_COMPARE[type(op)], self.node(rhs)) for op, rhs in zip(node.ops, node.comparators)]
        source = self.source

        def run(scope: Scope) -> bool:
            a = left(scope)
            for op, rhs in pairs:
                b = rhs(scope)
                if not op(a, b, source):
                    return False
                a = b
            return True

        guard = self._guard(node)
        if guard is not None:
            setattr(run, "guard", guard)
        return run

    def _guard(self, node: ast.AST) -> Optional[EqualityGuard]:
        """The :class:`EqualityGuard` a comparison ``$it.field == value`` (either way round) makes, if any."""
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)):
            return None
        for field_side, value_side in ((node.left, node.comparators[0]), (node.comparators[0], node.left)):
            chain = _chain(field_side)
            if chain is None or len(chain) != 2 or chain[0] != "it":
                continue
            parts = list(ast.walk(value_side))
            roots = {part.id[len(_ROOT_PREFIX):] for part in parts
                     if isinstance(part, ast.Name) and part.id.startswith(_ROOT_PREFIX)}
            if any(isinstance(part, ast.Call) for part in parts) or roots & {"it", "i"}:
                return None
            return EqualityGuard(chain[1], self.node(value_side), frozenset(roots))
        return None

    def _IfExp(self, node: ast.IfExp) -> Evaluator:
        test, body, orelse = self.node(node.test), self.node(node.body), self.node(node.orelse)
        return lambda scope: body(scope) if truthy(test(scope)) else orelse(scope)

    def _Call(self, node: ast.Call) -> Evaluator:
        assert isinstance(node.func, ast.Name)
        name = node.func.id[len(_FUNC_PREFIX):]
        spec = FUNCTIONS.get(name)
        source = self.source
        if spec is None:
            # A contract-defined function (`defs`), resolved by the world at run time and
            # verified by the checker against the contract.
            self.functions.add(name)
            self.calls.add((name, None))
            user_args = [self.node(arg) for arg in node.args]

            def run_def(scope: Scope) -> Any:
                values = [a(scope) for a in user_args]  # arguments belong to the caller's evaluation
                budget = _BUDGET  # the def body is nested work: it charges the caller's budget
                budget.hold += 1
                try:
                    return scope.world.call_def(name, values, source)
                finally:
                    budget.hold -= 1

            return run_def
        count = len(node.args)
        if count < spec.min_args or (spec.max_args is not None and count > spec.max_args):
            # Only valid if the contract defines its own `name` (checked at run time and by the checker).
            self.functions.add(name)
            self.calls.add((name, None))
            self.arity_errors.add((name, spec.signature))
            shadow_args = [self.node(arg) for arg in node.args]

            def shadow_or_fail(scope: Scope) -> Any:
                if scope.world is not None and scope.world.defines(name):
                    return scope.world.call_def(name, [a(scope) for a in shadow_args], source)
                raise ExprError(f"wrong number of arguments: ${spec.signature}", source)

            return shadow_or_fail
        self.functions.add(name)
        first = node.args[0] if node.args else None
        symbol = first.id if isinstance(first, ast.Name) and not first.id.startswith("__") else None
        self.calls.add((name, symbol))
        args = []
        for index, arg in enumerate(node.args):
            if index not in spec.lazy:
                args.append(self.node(arg))
                continue
            # $it and $i inside a per-item argument are bound by the function, not the caller.
            saved = (self.roots, self.paths, self.comparisons)
            self.roots, self.paths, self.comparisons = set(), set(), set()
            args.append(self.node(arg))
            inner_roots, inner_paths, inner_cmp = self.roots, self.paths, self.comparisons
            self.roots, self.paths, self.comparisons = saved
            bound = ("it", "i", "outer")
            self.roots |= inner_roots - set(bound)
            self.paths |= {p for p in inner_paths if p[0] not in bound}
            self.item_paths |= {(name, symbol, p) for p in inner_paths if p[0] == "it"}
            self.comparisons |= {c for c in inner_cmp if c[0][0] not in bound}
            self.item_comparisons |= {(name, symbol, c[0], c[1]) for c in inner_cmp if c[0][0] == "it"}

        def run(scope: Scope) -> Any:
            world = scope.world
            if world is not None and world.defines(name):  # the contract's own def wins over the built-in
                return world.call_def(name, [a(scope) for a in args], source)
            return spec.impl(Call(name, args, scope, source))

        return run


def evaluate(source: str, scope: Optional[Scope] = None) -> Any:
    """Compile (cached) and evaluate ``source``."""
    return compile_expr(source)(scope or Scope())


def resolve(value: Any, scope: Scope) -> Any:
    """Resolve a contract value: text with ``{$...}`` renders as a template, expression strings
    evaluate, containers resolve deeply."""
    if isinstance(value, str) and "{$" in value:
        from .template import compile_template

        return compile_template(value, None).render(scope)
    if is_expr(value):
        return compile_expr(value)(scope)
    if isinstance(value, list):
        return [resolve(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, scope) for key, item in value.items()}
    return value


# Built-in functions register themselves on import.
from . import functions as _functions  # noqa: E402,F401
