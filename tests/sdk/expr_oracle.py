"""The reference evaluator the compiled expression language is checked against.

This is the closure compiler the language shipped with before expressions were compiled to Python code, kept
verbatim: one closure per syntax node, evaluated by calling them. It shares only what the compiled language did
not replace — parsing (``_preprocess``, ``_restore_words``, the node whitelist) and the value helpers (``attr``, the operators,
``Call``) — so a difference between the two is a difference in compilation, never in a helper both call.
Used by the differential tests only.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from fg_env.sdk.expr_base import _BUDGET, EVAL_BUDGET, ExprError, charge, truthy
from fg_env.sdk.expr_calls import FUNCTIONS, Call, EqualityGuard, Evaluator
from fg_env.sdk.expr_codegen import _FUNC_PREFIX, _LITERAL_NAMES, _ROOT_PREFIX, _chain
from fg_env.sdk.expr_compile import _ALLOWED, _MAX_NODES, _MAX_SOURCE, _preprocess, _restore_words
from fg_env.sdk.expr_scope import Scope
from fg_env.sdk.expr_values import _BINARY, _COMPARE, _describe, _number, attr
from fg_env.sdk.syntax_hints import syntax_message


@dataclass(frozen=True)
class OracleExpr:
    source: str
    run: Evaluator
    roots: FrozenSet[str]
    functions: FrozenSet[str]
    symbols: FrozenSet[str]
    paths: FrozenSet[Tuple[str, ...]]
    calls: FrozenSet[Tuple[str, Optional[str]]]
    item_paths: FrozenSet[Tuple[str, Optional[str], Tuple[str, ...]]]
    comparisons: FrozenSet[Tuple[Tuple[str, ...], str]]
    item_comparisons: FrozenSet[Tuple[str, Optional[str], Tuple[str, ...], str]]
    arity_errors: FrozenSet[Tuple[str, str]]
    methods: FrozenSet[Tuple[str, str, int]]

    def __call__(self, scope: Scope) -> Any:
        budget = _BUDGET
        if budget.hold:
            if budget.shared and budget.hold == 1:
                budget.cap = min(budget.limit, budget.used + EVAL_BUDGET)
            budget.used += 1
            if budget.used > budget.limit or budget.used > budget.cap:
                charge(0, self.source)
        else:
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


def compile_oracle(source: str) -> OracleExpr:
    if not isinstance(source, str) or not source.strip():
        raise ExprError("expression is empty", str(source))
    source = source.strip()
    if len(source) > _MAX_SOURCE:
        raise ExprError("expression is too long", source[:60] + "…")
    try:
        # The public language permits leading !; preprocessing inserts whitespace.
        tree = ast.parse(_preprocess(source).strip(), mode="eval")
    except SyntaxError as exc:
        raise ExprError(syntax_message(source, str(exc.msg)), source) from None
    except (RecursionError, MemoryError):
        raise ExprError("expression is nested too deeply", source) from None
    except ValueError as exc:
        raise ExprError(f"syntax error: {exc}", source) from None
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_NODES:
        raise ExprError("expression is too large", source)
    _restore_words(nodes)
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"unsupported syntax ({type(node).__name__})", source)
        if isinstance(node, ast.Call) and (node.keywords or not (
            isinstance(node.func, ast.Name) and node.func.id.startswith(_FUNC_PREFIX) or _root_method(node.func)
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
    return OracleExpr(source, run, frozenset(compiler.roots), frozenset(compiler.functions),
                      frozenset(compiler.symbols), frozenset(compiler.paths), frozenset(compiler.calls),
                      frozenset(compiler.item_paths), frozenset(compiler.comparisons),
                      frozenset(compiler.item_comparisons), frozenset(compiler.arity_errors),
                      frozenset(compiler.methods))


def _root_method(func: ast.AST) -> bool:
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id.startswith(_ROOT_PREFIX)


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
        self.methods: set = set()

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

    def _method(self, func: ast.Attribute, nodes: Sequence[ast.AST]) -> Evaluator:
        assert isinstance(func.value, ast.Name)
        root, name, source = func.value.id[len(_ROOT_PREFIX):], func.attr, self.source
        self.roots.add(root)
        self.paths.add((root, name))
        self.methods.add((root, name, len(nodes)))
        args = [self.node(arg) for arg in nodes]

        def run(scope: Scope) -> Any:
            target = scope.root(root, source)
            caller = getattr(target, "expr_call", None)
            if caller is None:
                raise ExprError(f"${root}.{name} cannot be called; read it as ${root}.{name}", source)
            return caller(name, [arg(scope) for arg in args], source)

        return run

    def _Call(self, node: ast.Call) -> Evaluator:
        if isinstance(node.func, ast.Attribute):
            return self._method(node.func, node.args)
        assert isinstance(node.func, ast.Name)
        name = node.func.id[len(_FUNC_PREFIX):]
        spec = FUNCTIONS.get(name)
        source = self.source
        if spec is None:
            self.functions.add(name)
            self.calls.add((name, None))
            user_args = [self.node(arg) for arg in node.args]

            def run_def(scope: Scope) -> Any:
                values = [a(scope) for a in user_args]
                budget = _BUDGET
                budget.hold += 1
                try:
                    return scope.world.call_def(name, values, source)
                finally:
                    budget.hold -= 1

            return run_def
        count = len(node.args)
        if count < spec.min_args or (spec.max_args is not None and count > spec.max_args):
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
            if world is not None and world.defines(name):
                return world.call_def(name, [a(scope) for a in args], source)
            return spec.impl(Call(name, args, scope, source))

        return run
