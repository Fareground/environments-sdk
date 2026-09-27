"""Conservative dimensional checking of output arithmetic; unsupported expressions are explicitly unchecked."""
from __future__ import annotations

import ast
from typing import Any

from ..contract.quantity import Quantity
from ..expr import ExprError
from ..expr.compile import FUNC_PREFIX, ROOT_PREFIX, syntax_tree


def _literal(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and not node.id.startswith((ROOT_PREFIX, FUNC_PREFIX)):
        return node.id
    if isinstance(node, ast.Dict):
        return {_literal(k): _literal(v) for k, v in zip(node.keys, node.values) if k is not None}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal(node.operand)
    raise ValueError("unit expression is not a static unit declaration")


def check_quantities(checker: Any) -> None:
    c = checker.c
    if not any(spec.quantity for spec in [*c.inputs.values(), *c.outputs.values()]):
        return  # Legacy display labels retain their existing, explicitly non-dimensional semantics.
    for name, spec in [*(('inputs.' + k, v) for k, v in c.inputs.items()),
                       *(('outputs.' + k, v) for k, v in c.outputs.items())]:
        if spec.quantity and spec.type not in ({'number', 'int'} if name.startswith('inputs.')
                                             else {'number', 'int', 'any'}):
            checker.error(name + '.quantity', 'quantity metadata requires a numeric input or output')

    def compatible(left: Quantity | None, right: Quantity | None) -> Quantity | None:
        if left is None or right is None:
            return None
        if not left.same(right):
            raise ValueError('incompatible dimensions, scales or absolute/interval kinds; use $convert explicitly')
        return left

    visiting: set[str] = set()

    def output(name: str) -> Quantity | None:
        if name not in c.outputs or name in visiting or len(visiting) >= 64:
            return None
        visiting.add(name)
        try:
            return infer(syntax_tree(c.outputs[name].expr))
        finally:
            visiting.remove(name)

    def infer(node: ast.expr) -> Quantity | None:
        if isinstance(node, ast.Constant):
            return Quantity() if type(node.value) in (int, float) else None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == ROOT_PREFIX + 'inputs':
                spec = c.inputs.get(node.attr)
                return spec.quantity if spec else None
            if node.value.id == ROOT_PREFIX + 'outputs':
                return output(node.attr)
        if isinstance(node, ast.UnaryOp):
            return infer(node.operand) if not isinstance(node.op, ast.Not) else None
        if isinstance(node, ast.BinOp):
            left, right = infer(node.left), infer(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
                if isinstance(node.op, ast.Sub) and left.absolute and right.absolute:
                    compatible(left, right)
                    return Quantity(dimensions=left.dimensions, scale=left.scale)
                if isinstance(node.op, ast.Add) and left.absolute and right.absolute:
                    raise ValueError('two absolute quantities cannot be added; add a temperature interval instead')
                if isinstance(node.op, (ast.Add, ast.Sub)) and left.absolute and not right.absolute:
                    compatible(Quantity(dimensions=left.dimensions, scale=left.scale), right)
                    return left
                return compatible(left, right)
            if isinstance(node.op, ast.Mult):
                return left.product(right)
            if isinstance(node.op, (ast.Div, ast.FloorDiv)):
                return left.product(right, -1)
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and type(node.right.value) is int:
                if left.offset:
                    raise ValueError('offset units cannot be raised to powers; convert to kelvin first')
                exponent = node.right.value
                if abs(exponent) > 12:
                    raise ValueError('quantity powers must be between -12 and 12')
                return Quantity(dimensions={k: v * exponent for k, v in left.dimensions.items()},
                                scale=left.scale ** exponent)
            return None
        if isinstance(node, ast.Compare):
            values = [infer(part) for part in [node.left, *node.comparators]]
            for left, right in zip(values, values[1:]):
                compatible(left, right)
            return None
        if isinstance(node, ast.IfExp):
            infer(node.test)
            return compatible(infer(node.body), infer(node.orelse))
        if isinstance(node, ast.Call):
            args = [infer(arg) for arg in node.args]
            name = node.func.id.removeprefix(FUNC_PREFIX) if isinstance(node.func, ast.Name) else ''
            if name in {'convert', 'quantity', 'convert_currency'}:
                if len(node.args) < (2 if name == 'quantity' else 3):
                    return None
                try:
                    source_value = _literal(node.args[1])
                    target_value = _literal(node.args[2]) if name != 'quantity' else source_value
                except (ValueError, TypeError):
                    return None
                source = Quantity.model_validate(source_value)
                target = Quantity.model_validate(target_value)
                if args[0] is not None and not isinstance(node.args[0], ast.Constant) \
                        and (name != 'quantity' or args[0].dimensions or args[0].absolute):
                    compatible(args[0], source)
                if name == 'convert' and (source.dimensions != target.dimensions or source.absolute != target.absolute):
                    raise ValueError('conversion has incompatible dimensions or absolute/interval kinds')
                return target
            if name in {'round', 'floor', 'ceil', 'abs'} and args:
                return args[0]
            if name in {'min', 'max'} and args:
                result = args[0]
                for arg in args[1:]:
                    result = compatible(result, arg)
                return result
            return None
        # Still visit unsupported containers/calls so nested incompatible arithmetic is not hidden.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                infer(child)
        return None

    for name, spec in c.outputs.items():
        for suffix, expr in [('', spec.expr), *([('.series', spec.series)] if isinstance(spec.series, str) else [])]:
            path = f'outputs.{name}{suffix}'
            try:
                inferred = infer(syntax_tree(expr))
                if spec.quantity:
                    if inferred is None:
                        checker.warn(path, 'quantity dimensions are unchecked for this expression',
                                     'use supported typed input/output arithmetic and explicit conversions; '
                                     'world state, reducers and custom function semantics need independent checks')
                    else:
                        compatible(inferred, spec.quantity)
                elif inferred and (inferred.dimensions or inferred.scale != 1 or inferred.absolute):
                    checker.warn(path, 'output arithmetic has a quantity but its expected dimensions are undeclared',
                                 'declare output.quantity; output.unit is only a display label')
            except (ValueError, OverflowError) as exc:
                checker.error(path + '.quantity', str(exc))
            except ExprError:
                pass  # Ordinary expression validation supplies syntax/name diagnostics.
