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
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Untrusted",
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


class Untrusted(str):
    """Text written by a participant. It keeps that provenance wherever it is stored and
    renders wrapped in «» so other agents read it as information, never instructions."""

    __slots__ = ()


class ExprError(ValueError):
    """An expression is malformed or cannot be evaluated against the current state."""

    def __init__(self, message: str, source: Optional[str] = None):
        self.source = source
        self.detail = message
        super().__init__(f"{message} — in `{source}`" if source else message)


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

    def entity(self, entity_id: str) -> Any:
        return None

    def records(self, name: str) -> List[Any]:
        raise ExprError(f"no record '{name}' exists in this context")

    def visible_records(self, name: str, viewer: Any) -> List[Any]:
        return self.records(name)

    def events(self, name: Optional[str]) -> List[Any]:
        return []

    def relation(self, a: Any, b: Any, kind: str) -> Optional[float]:
        return None

    def neighbors(self, entity: Any, kind: str) -> List[Any]:
        return []

    def distance(self, a: Any, b: Any) -> float:
        raise ExprError("this environment declares no space")

    def is_type(self, name: str) -> bool:
        return False

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return type_name == ancestor

    def call_def(self, name: str, args: List[Any], source: str) -> Any:
        """Call a contract-defined function (``defs``). The empty world has none."""
        from difflib import get_close_matches

        hint = get_close_matches(name, list(FUNCTIONS), n=1)
        raise ExprError(f"unknown function ${name}" + (f" — did you mean ${hint[0]}?" if hint else ""), source)


_EMPTY_WORLD = World()


@dataclass(frozen=True)
class Scope:
    """Values visible to an expression: named roots plus the world to query."""

    vars: Mapping[str, Any] = field(default_factory=dict)
    world: World = _EMPTY_WORLD

    def child(self, **values: Any) -> "Scope":
        merged = dict(self.vars)
        merged.update(values)
        return Scope(merged, self.world)

    def root(self, name: str, source: str) -> Any:
        try:
            return self.vars[name]
        except KeyError:
            available = ", ".join(f"${k}" for k in sorted(self.vars)) or "none"
            raise ExprError(f"${name} is not available here (available: {available})", source) from None


# ---------------------------------------------------------------------------
# Value access
# ---------------------------------------------------------------------------


def attr(obj: Any, name: str, source: Optional[str] = None) -> Any:
    """Read ``obj.name`` under expression semantics (entities, dicts, records)."""
    if name.startswith("_"):
        raise ExprError(f"private field '{name}' cannot be read", source)
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
        key = _entity_id(item)
        return any(_entity_id(x) == key for x in container)
    raise ExprError(f"'in' needs a list or text on the right, got {_describe(container)}", source)


def _add(a: Any, b: Any, source: str) -> Any:
    if isinstance(a, str) and isinstance(b, str):
        joined = str.__add__(a, b)
        return Untrusted(joined) if isinstance(a, Untrusted) or isinstance(b, Untrusted) else joined
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return _finite(_number(a, source) + _number(b, source), source)


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
    try:
        return _finite(a ** b, source)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ExprError(f"power failed: {exc}", source) from None


def _arith(op: Callable[[Any, Any], Any]) -> Callable[[Any, Any, str], Any]:
    return lambda a, b, source: _finite(op(_number(a, source), _number(b, source)), source)


_BINARY: Dict[type, Callable[[Any, Any, str], Any]] = {
    ast.Add: _add,
    ast.Sub: _arith(operator.sub),
    ast.Mult: _arith(operator.mul),
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
        return self.nodes[index](self.scope.child(it=item, i=position, outer=self.scope.vars.get("it")))

    def collection(self, index: int = 0) -> List[Any]:
        value = self.arg(index)
        if isinstance(value, str):
            if not self.scope.world.is_type(value):
                raise ExprError(
                    f"${self.name}: '{value}' is not an entity type (pass a type name or a list)",
                    self.source,
                )
            return list(self.scope.world.entities_of(value))
        if value is None:
            return []
        if isinstance(value, Mapping):
            return list(value.values())
        if isinstance(value, (list, tuple)):
            return list(value)
        if hasattr(value, "entity_type"):
            return [value]
        raise ExprError(f"${self.name}: expected an entity type or a list, got {_describe(value)}", self.source)

    def filtered(self, index: int = 0, where: Optional[int] = None) -> List[Any]:
        items = self.collection(index)
        if where is None or where >= len(self.nodes):
            return items
        return [item for pos, item in enumerate(items) if truthy(self.each(where, item, pos))]

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

    def __call__(self, scope: Scope) -> Any:
        try:
            return self.run(scope)
        except ExprError:
            raise
        except RecursionError:
            raise ExprError("evaluation nested too deeply", self.source) from None
        except (ArithmeticError, IndexError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ExprError(f"could not evaluate: {type(exc).__name__}: {exc}", self.source) from None


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
    except RecursionError:
        raise ExprError("expression is nested too deeply", source) from None
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
    run = compiler.node(tree.body)
    return Expr(source, run, frozenset(compiler.roots), frozenset(compiler.functions),
                frozenset(compiler.symbols), frozenset(compiler.paths), frozenset(compiler.calls),
                frozenset(compiler.item_paths), frozenset(compiler.comparisons),
                frozenset(compiler.item_comparisons))


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
        if isinstance(node.op, ast.And):
            return lambda scope: all(truthy(v(scope)) for v in values)
        return lambda scope: any(truthy(v(scope)) for v in values)

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

        return run

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
            return lambda scope: scope.world.call_def(name, [a(scope) for a in user_args], source)
        count = len(node.args)
        if count < spec.min_args or (spec.max_args is not None and count > spec.max_args):
            raise ExprError(f"wrong number of arguments: ${spec.signature}", source)
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
