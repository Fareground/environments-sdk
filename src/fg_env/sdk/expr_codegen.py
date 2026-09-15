"""Compiling a validated expression tree to Python code, once per expression.

Each evaluator — the whole expression, and every argument of a built-in call (functions read their arguments
through :class:`~.expr_calls.Call`, eagerly or per item) — becomes one Python function of the scope. Inside it
the tree is flattened into statements over local temporaries, evaluated in exactly the order the language
defines (left to right, short-circuiting ``and``/``or``/comparison chains and ``if … else``).

Safety: the generated source is built only from this module's fixed statement shapes, generated identifiers
(``_n3``, ``_t7``, ``_k2``) and whole-number indices. No text from an expression reaches it: every literal,
root name, field name and symbol is bound in the function's namespace as a constant (``_k2``). The namespace
has no builtins — only the helpers listed in :func:`_helpers` — so compiled code can do nothing but what
those helpers do. Syntax was whitelisted before compilation (see :mod:`.expr_compile`).

Semantics: a statement shape reads a value directly only where the result is certain to equal the helper's
(an entity's declared property, a ``$world`` property, an in-range list element, arithmetic and comparison on
plain whole numbers or text); every other case calls the same helper the language always used, so values,
errors, work-budget charges and random draws are the ones the helper gives.
"""
from __future__ import annotations

import ast
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .expr_base import _BUDGET, ExprError
from .expr_calls import FUNCTIONS, Call, EqualityGuard, Evaluator
from .expr_scope import Scope
from .expr_values import _BINARY, _COMPARE, _ENTITY_FIELDS, _Entity, _add, _eq, _in, _index, _mul, _number, _pow, attr

__all__ = ["Codegen"]

#: Deepest statement nesting inside one generated function; a deeper subtree becomes a function of its own
#: (Python limits indentation depth).
_MAX_NESTING = 24
#: Whole numbers below this size multiply without the bit-length check (the product is far below the limit).
_SMALL = 2 ** 62
_ROOT_PREFIX = "__r_"
_FUNC_PREFIX = "__f_"
_LITERAL_NAMES = {"true": True, "false": False, "null": None}
_ENTITY_ATTRIBUTES = {"id": "id", "name": "name", "type": "entity_type", "alive": "alive", "at": "location_id"}
_ORDERED = {ast.Lt: ("<", "_lt"), ast.LtE: ("<=", "_le"), ast.Gt: (">", "_gt"), ast.GtE: (">=", "_ge")}
_INT_ARITHMETIC = {ast.Add: ("+", "_add"), ast.Sub: ("-", "_sub"), ast.FloorDiv: ("//", "_floordiv"),
                   ast.Mod: ("%", "_mod"), ast.Div: ("/", "_truediv")}


def _root(scope: Scope, name: str, source: str) -> Any:
    return scope.root(name, source)


def _call_def(scope: Scope, name: str, values: List[Any], source: str) -> Any:
    budget = _BUDGET  # the def body is nested work: it charges the caller's budget
    budget.hold += 1
    try:
        return scope.world.call_def(name, values, source)
    finally:
        budget.hold -= 1


def _arity(signature: str, source: str) -> None:
    raise ExprError(f"wrong number of arguments: ${signature}", source)


def _negate(value: Any, source: str) -> Any:
    return -1 * _number(value, source)


def _plus(value: Any, source: str) -> Any:
    return 1 * _number(value, source)


def _helpers() -> Dict[str, Any]:
    """Everything compiled code may name besides its own constants and functions."""
    from .world_parts import PropsView  # the world's parts import the language: bound on first compile

    return {
        "__builtins__": {}, "_type": type, "_len": len, "_int": int, "_str": str, "_float": float, "_list": list,
        "_Entity": _Entity, "_PropsView": PropsView, "_Call": Call, "_attr": attr, "_index": _index,
        "_root": _root, "_call_def": _call_def, "_arity": _arity, "_negate": _negate, "_plus": _plus,
        "_add": _add, "_sub": _BINARY[ast.Sub], "_mul": _mul, "_truediv": _BINARY[ast.Div],
        "_floordiv": _BINARY[ast.FloorDiv], "_mod": _BINARY[ast.Mod], "_pow": _pow,
        "_eq": _eq, "_in": _in, "_lt": _COMPARE[ast.Lt], "_le": _COMPARE[ast.LtE], "_gt": _COMPARE[ast.Gt],
        "_ge": _COMPARE[ast.GtE], "_SMALL": _SMALL, "_NSMALL": -_SMALL,
    }


_HELPERS: Optional[Dict[str, Any]] = None


class _Function:
    """One generated function while its body is being written."""

    __slots__ = ("name", "lines", "temps", "reads_roots")

    def __init__(self, name: str):
        self.name = name
        self.lines: List[str] = []
        self.temps = 0
        self.reads_roots = False

    def source(self) -> str:
        head = [f"def {self.name}(scope):"] + (["    _V = scope.vars"] if self.reads_roots else [])
        return "\n".join(head + self.lines)


def _chain(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """``$a.b.c`` → ``("a", "b", "c")``; None when the chain does not start at a root."""
    fields: List[str] = []
    while isinstance(node, ast.Attribute):
        fields.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id.startswith(_ROOT_PREFIX):
        return (node.id[len(_ROOT_PREFIX):], *reversed(fields))
    return None


class Codegen:
    """Compiles one expression's tree, recording what it reads (roots, paths, calls, …) as it goes.

    Nodes are visited in the order the language has always compiled them, so the first compile error found
    and the recorded facts are the same as ever."""

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
        self._namespace: Dict[str, Any] = {}
        self._strings: Dict[str, str] = {}
        self._written: List[_Function] = []
        self._fn = _Function("")
        self._depth = 0
        self._arguments: List[Tuple[str, List[str]]] = []
        self._guards: List[Tuple[str, str, str, FrozenSet[str]]] = []

    # -- output ----------------------------------------------------------------------------------------

    def compile(self, tree: ast.AST) -> Evaluator:
        """The evaluator of the whole expression."""
        global _HELPERS
        main = self._function(tree, guarded=True)
        if _HELPERS is None:
            _HELPERS = _helpers()
        namespace = {**_HELPERS, **self._namespace}
        exec(compile("\n".join(fn.source() for fn in self._written), "<fg_env expression>", "exec"), namespace)
        for name, members in self._arguments:
            namespace[name] = [namespace[member] for member in members]
        for owner, field, value, roots in self._guards:
            setattr(namespace[owner], "guard", EqualityGuard(field, namespace[value], roots))
        run: Evaluator = namespace[main]
        return run

    def _line(self, text: str) -> None:
        self._fn.lines.append("    " * self._depth + text)

    def _temp(self) -> str:
        self._fn.temps += 1
        return f"_t{self._fn.temps}"

    def _const(self, value: Any) -> str:
        """A name bound to ``value`` in the compiled code's namespace (one per distinct text)."""
        if type(value) is str and value in self._strings:
            return self._strings[value]
        name = f"_k{len(self._namespace)}"
        self._namespace[name] = value
        if type(value) is str:
            self._strings[value] = name
        return name

    def _function(self, node: ast.AST, guarded: bool) -> str:
        """Write ``node`` as a function of its own; its name. ``guarded``: the function is an evaluator a
        collection function may read an :class:`EqualityGuard` from."""
        outer, depth = self._fn, self._depth
        fn = self._fn = _Function(f"_n{len(self._written)}")
        self._written.append(fn)
        self._depth = 1
        result = self.node(node)
        self._line(f"return {result}")
        self._fn, self._depth = outer, depth
        if guarded:
            guard = self._guard(node.values[0] if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And)
                                else node)
            if guard is not None:
                self._guards.append((fn.name, *guard))
        return fn.name

    # -- nodes -----------------------------------------------------------------------------------------

    def node(self, node: ast.AST) -> str:
        """Write the statements computing ``node``; an operand holding its value (a name or a keyword)."""
        if self._depth > _MAX_NESTING:
            name = self._function(node, guarded=False)
            value = self._temp()
            self._line(f"{value} = {name}(scope)")
            return value
        method = getattr(self, "_" + type(node).__name__)
        result: str = method(node)
        return result

    def _Constant(self, node: ast.Constant) -> str:
        value = node.value
        if not isinstance(value, (int, float, str, bool)) and value is not None:
            raise ExprError("unsupported literal", self.source)
        return self._const(value)

    def _Name(self, node: ast.Name) -> str:
        name = node.id
        if name.startswith(_ROOT_PREFIX):
            root = name[len(_ROOT_PREFIX):]
            self.roots.add(root)
            key, value = self._const(root), self._temp()
            self._fn.reads_roots = True
            self._line(f"{value} = _V[{key}] if {key} in _V else _root(scope, {key}, {self._source()})")
            return value
        if name.startswith(_FUNC_PREFIX):
            raise ExprError(f"${name[len(_FUNC_PREFIX):]} is a function; call it with (...)", self.source)
        if name in _LITERAL_NAMES:
            return self._const(_LITERAL_NAMES[name])
        self.symbols.add(name)
        return self._const(name)

    def _source(self) -> str:
        return self._const(self.source)

    def _Attribute(self, node: ast.Attribute) -> str:
        chain = _chain(node)
        if chain is not None:
            self.paths.add(chain)
        base, name = self.node(node.value), node.attr
        key, value, source = self._const(name), self._temp(), self._source()
        if name in _ENTITY_FIELDS:
            self._line(f"{value} = {base}.{_ENTITY_ATTRIBUTES[name]} if _type({base}) is _Entity "
                       f"else _attr({base}, {key}, {source})")
            return value
        self._line(f"if _type({base}) is _Entity and {key} in {base}.properties:")
        self._line(f"    {value} = {base}.properties[{key}]")
        self._line(f"elif _type({base}) is _PropsView and {key} in {base}._world.props:")
        self._line(f"    {value} = {base}._world.props[{key}]")
        self._line("else:")
        self._line(f"    {value} = _attr({base}, {key}, {source})")
        return value

    def _Subscript(self, node: ast.Subscript) -> str:
        base, key = self.node(node.value), self.node(node.slice)
        value = self._temp()
        self._line(f"if _type({base}) is _list and _type({key}) is _int and -_len({base}) <= {key} < _len({base}):")
        self._line(f"    {value} = {base}[{key}]")
        self._line("else:")
        self._line(f"    {value} = _index({base}, {key}, {self._source()})")
        return value

    def _List(self, node: ast.List) -> str:
        items = [self.node(item) for item in node.elts]
        value = self._temp()
        self._line(f"{value} = [{', '.join(items)}]")
        return value

    _Tuple = _List

    def _Dict(self, node: ast.Dict) -> str:
        keys: List[str] = []
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, (str, int, float)) and not isinstance(key.value, bool):
                keys.append(str(key.value))
            elif isinstance(key, ast.Name) and not key.id.startswith("__"):
                keys.append(key.id)
            else:
                raise ExprError("map keys must be text or numbers, like {wage: 3, 'job years': 2}", self.source)
        values = [self.node(item) for item in node.values]
        value = self._temp()
        self._line(f"{value} = {{{', '.join(f'{self._const(k)}: {v}' for k, v in zip(keys, values))}}}")
        return value

    def _BinOp(self, node: ast.BinOp) -> str:
        left, right = self.node(node.left), self.node(node.right)
        value, source, op = self._temp(), self._source(), type(node.op)
        ints = f"_type({left}) is _int and _type({right}) is _int"
        if op in _INT_ARITHMETIC:
            symbol, helper = _INT_ARITHMETIC[op]
            nonzero = f" and {right}" if op in (ast.Div, ast.FloorDiv, ast.Mod) else ""
            self._line(f"{value} = {left} {symbol} {right} if {ints}{nonzero} else {helper}({left}, {right}, {source})")
        elif op is ast.Mult:
            small = f"_NSMALL < {left} < _SMALL and _NSMALL < {right} < _SMALL"
            self._line(f"{value} = {left} * {right} if {ints} and {small} else _mul({left}, {right}, {source})")
        else:
            self._line(f"{value} = _pow({left}, {right}, {source})")
        return value

    def _UnaryOp(self, node: ast.UnaryOp) -> str:
        operand, value = self.node(node.operand), self._temp()
        if isinstance(node.op, ast.Not):
            self._line(f"{value} = not {operand}")
        elif isinstance(node.op, ast.USub):
            self._line(f"{value} = -{operand} if _type({operand}) is _int else _negate({operand}, {self._source()})")
        else:
            self._line(f"{value} = {operand} if _type({operand}) is _int else _plus({operand}, {self._source()})")
        return value

    def _BoolOp(self, node: ast.BoolOp) -> str:
        # Like Python: `a or b` gives the first truthy value (a default), `a and b` the first falsy one.
        value, depth = self._temp(), self._depth
        test = f"if {value}:" if isinstance(node.op, ast.And) else f"if not {value}:"
        self._line(f"{value} = {self.node(node.values[0])}")
        for item in node.values[1:]:
            self._line(test)
            self._depth += 1
            self._line(f"{value} = {self.node(item)}")
        self._depth = depth
        return value

    def _word(self, node: ast.AST) -> List[str]:
        if isinstance(node, ast.Name) and not node.id.startswith("__") and node.id not in _LITERAL_NAMES:
            return [node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [w for item in node.elts for w in self._word(item)]
        return []

    def _Compare(self, node: ast.Compare) -> str:
        operands = [node.left, *node.comparators]
        for a, b in zip(operands, operands[1:]):
            for chain_node, other in ((a, b), (b, a)):
                chain = _chain(chain_node)
                if chain is not None and len(chain) > 1:
                    for word in self._word(other):
                        self.comparisons.add((chain, word))
        value, depth = self._temp(), self._depth
        left = self.node(node.left)
        for position, (op, comparator) in enumerate(zip(node.ops, node.comparators)):
            if position:
                self._line(f"if {value}:")
                self._depth += 1
            right = self.node(comparator)
            self._line(f"{value} = {self._comparison(type(op), left, right)}")
            left = right
        self._depth = depth
        return value

    def _comparison(self, op: type, left: str, right: str) -> str:
        """An expression for one comparison of two computed operands (always a bool, as the helpers give)."""
        source = self._source()
        if op in (ast.Eq, ast.NotEq):
            symbol, negation = ("==", "") if op is ast.Eq else ("!=", "not ")
            plain = (f"(_type({left}) is _str and _type({right}) is _str) "
                     f"or (_type({left}) is _int and _type({right}) is _int)")
            return f"{left} {symbol} {right} if {plain} else {negation}_eq({left}, {right})"
        if op in _ORDERED:
            symbol, helper = _ORDERED[op]
            numbers = (f"(_type({left}) is _int or _type({left}) is _float) "
                       f"and (_type({right}) is _int or _type({right}) is _float)")
            return f"{left} {symbol} {right} if {numbers} else {helper}({left}, {right}, {source})"
        return f"{'not ' if op is ast.NotIn else ''}_in({left}, {right}, {source})"

    def _guard(self, node: ast.AST) -> Optional[Tuple[str, str, FrozenSet[str]]]:
        """``(field, value function, roots)`` of the :class:`EqualityGuard` a comparison ``$it.field == value``
        (either way round) makes, if any."""
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
            return chain[1], self._function(value_side, guarded=False), frozenset(roots)
        return None

    def _IfExp(self, node: ast.IfExp) -> str:
        test, value = self.node(node.test), self._temp()
        self._line(f"if {test}:")
        self._depth += 1
        self._line(f"{value} = {self.node(node.body)}")
        self._depth -= 1
        self._line("else:")
        self._depth += 1
        self._line(f"{value} = {self.node(node.orelse)}")
        self._depth -= 1
        return value

    def _Call(self, node: ast.Call) -> str:
        assert isinstance(node.func, ast.Name)
        name = node.func.id[len(_FUNC_PREFIX):]
        spec = FUNCTIONS.get(name)
        key, value, source = self._const(name), self._temp(), self._source()
        if spec is None:
            # A contract-defined function (`defs`), resolved by the world at run time and
            # verified by the checker against the contract.
            self.functions.add(name)
            self.calls.add((name, None))
            values = [self.node(arg) for arg in node.args]  # arguments belong to the caller's evaluation
            self._line(f"{value} = _call_def(scope, {key}, [{', '.join(values)}], {source})")
            return value
        count = len(node.args)
        if count < spec.min_args or (spec.max_args is not None and count > spec.max_args):
            # Only valid if the contract defines its own `name` (checked at run time and by the checker).
            self.functions.add(name)
            self.calls.add((name, None))
            self.arity_errors.add((name, spec.signature))
            self._line(f"if scope.world is not None and scope.world.defines({key}):")
            self._depth += 1
            values = [self.node(arg) for arg in node.args]
            self._line(f"{value} = scope.world.call_def({key}, [{', '.join(values)}], {source})")
            self._depth -= 1
            self._line("else:")
            self._line(f"    _arity({self._const(spec.signature)}, {source})")
            return value
        self.functions.add(name)
        first = node.args[0] if node.args else None
        symbol = first.id if isinstance(first, ast.Name) and not first.id.startswith("__") else None
        self.calls.add((name, symbol))
        members = []
        for index, arg in enumerate(node.args):
            if index not in spec.lazy:
                members.append(self._function(arg, guarded=True))
                continue
            # $it and $i inside a per-item argument are bound by the function, not the caller.
            saved = (self.roots, self.paths, self.comparisons)
            self.roots, self.paths, self.comparisons = set(), set(), set()
            members.append(self._function(arg, guarded=True))
            inner_roots, inner_paths, inner_cmp = self.roots, self.paths, self.comparisons
            self.roots, self.paths, self.comparisons = saved
            bound = ("it", "i", "outer")
            self.roots |= inner_roots - set(bound)
            self.paths |= {p for p in inner_paths if p[0] not in bound}
            self.item_paths |= {(name, symbol, p) for p in inner_paths if p[0] == "it"}
            self.comparisons |= {c for c in inner_cmp if c[0][0] not in bound}
            self.item_comparisons |= {(name, symbol, c[0], c[1]) for c in inner_cmp if c[0][0] == "it"}
        arguments = f"_a{len(self._arguments)}"
        self._arguments.append((arguments, members))
        impl = self._const(spec.impl)
        self._line(f"if scope.world is not None and scope.world.defines({key}):  # the contract's own def wins")
        self._line(f"    {value} = scope.world.call_def({key}, [{', '.join(f'{m}(scope)' for m in members)}], {source})")
        self._line("else:")
        self._line(f"    {value} = {impl}(_Call({key}, {arguments}, scope, {source}))")
        return value
