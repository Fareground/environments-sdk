"""Exact scalar affine SDE transitions when coefficients are constant in time.

For dx=(a*x+b)dt+s*dW, the conditional variance is s²*expm1(2*a*h)/(2*a).
For dx=a*x*dt+c*x*dW, the logarithm has drift a-c²/2 and variance c²*h.
Recognition is structural, never inferred by probing a nonlinear expression.
"""
from __future__ import annotations

import ast
import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

from .physics import _CompiledExpr


@lru_cache(maxsize=4096)
def affine(source: str, variable: str) -> Optional[Tuple[_CompiledExpr, _CompiledExpr]]:
    def parts(node: ast.AST) -> Optional[Tuple[str, str]]:
        if not any(isinstance(n, ast.Name) and n.id == variable for n in ast.walk(node)):
            return "0", ast.unparse(node)
        if isinstance(node, ast.Name):
            return "1", "0"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            pair = parts(node.operand)
            if pair is None:
                return None
            if isinstance(node.op, ast.UAdd):
                return pair
            return (f"-({pair[0]})" if pair[0] != "0" else "0",
                    f"-({pair[1]})" if pair[1] != "0" else "0")
        if not isinstance(node, ast.BinOp):
            return None
        left, right = parts(node.left), parts(node.right)
        if left is None or right is None:
            return None
        a, b = left
        c, d = right
        if isinstance(node.op, (ast.Add, ast.Sub)):
            op = "+" if isinstance(node.op, ast.Add) else "-"
            return f"({a}){op}({c})", f"({b}){op}({d})"
        if isinstance(node.op, ast.Mult):
            if a != "0" and c != "0":
                return None
            return f"({a})*({d})+({c})*({b})", f"({b})*({d})"
        if isinstance(node.op, ast.Div) and c == "0":
            return f"({a})/({d})", f"({b})/({d})"
        return None

    result = parts(ast.parse(source, mode="eval").body)
    if result is None:
        return None
    return _CompiledExpr(result[0]), _CompiledExpr(result[1])


def exact_transition(rate: _CompiledExpr, noise: _CompiledExpr, variable: str,
                     namespace: Dict[str, Any], value: float, h: float, normal: float) -> Optional[float]:
    drift, diffusion = affine(rate.source, variable), affine(noise.source, variable)
    if drift is None or diffusion is None:
        return None
    a, b = (expr.eval(namespace) for expr in drift)
    c, d = (expr.eval(namespace) for expr in diffusion)
    if c == 0:
        if a == 0:
            return value + b*h + d*math.sqrt(h)*normal
        gain = math.expm1(a*h)
        mean = value*math.exp(a*h) + (b/a)*gain
        variance_time = math.expm1(2*a*h)/(2*a)
        return mean + d*math.sqrt(variance_time)*normal
    if b == 0 and d == 0:
        return value * math.exp((a - c*c/2)*h + c*math.sqrt(h)*normal)
    return None
