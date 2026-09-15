"""Parsing, validating and compiling expressions: :func:`compile_expr` turns text into an :class:`Expr`.

The parser uses Python's ``ast`` module purely as a grammar. Nothing is executed as Python; only
whitelisted node types are interpreted.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, FrozenSet, List, Mapping, Optional, Tuple

from .expr_base import _BUDGET, EVAL_BUDGET, ExprError, charge, nested_free, truthy
from .expr_calls import _NO_KEY, FUNCTIONS, Call, EqualityGuard, Evaluator
from .expr_scope import Scope
from .expr_values import _BINARY, _COMPARE, _describe, _number, attr
from .syntax_hints import syntax_message

__all__ = ["Expr", "compile_expr"]

_MAX_SOURCE = 8_192
_MAX_NODES = 2_048
_ROOT_PREFIX = "__r_"
_FUNC_PREFIX = "__f_"

_LITERAL_NAMES = {"true": True, "false": False, "null": None}

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
        raise ExprError(syntax_message(source, "unclosed quote"), source)
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
        raise ExprError(syntax_message(source, str(exc.msg)), source) from None
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
            called = node.func.id if isinstance(node.func, ast.Name) and not node.keywords else None
            raise ExprError("only $functions can be called, with positional arguments"
                            + (f": write ${called}(...)" if called else ""), source)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and not node.value.id.startswith((_ROOT_PREFIX, _FUNC_PREFIX)) and node.value.id not in _LITERAL_NAMES:
            raise ExprError(f"'{node.value.id}.{node.attr}' reads a field of plain text: write ${node.value.id}.{node.attr}",
                            source)
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
