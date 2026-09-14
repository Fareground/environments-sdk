"""Math: trigonometry, logistic curves, interpolation, whole-number arithmetic, softmax."""
from __future__ import annotations

import bisect
import math
from typing import Any, Callable, List

from ..expr import MAX_INT_BITS, Call, ExprError, _pow, charge, function
from ._args import fail, int_arg, list_arg, number_arg, present_numbers


def _finite(call: Call, value: float) -> float:
    if not math.isfinite(value):
        raise fail(call, "the result is not a finite number")
    return value


def _unary(signature: str, doc: str, fn: Callable[[float], float]) -> None:
    def impl(call: Call) -> float:
        value = number_arg(call, 0)
        try:
            return _finite(call, fn(value))
        except (ValueError, OverflowError):
            raise fail(call, f"is not defined for {value}") from None

    function(signature, doc, min_args=1, max_args=1)(impl)


_unary("sin(x)", "Sine of x (radians).", math.sin)
_unary("cos(x)", "Cosine of x (radians).", math.cos)
_unary("tan(x)", "Tangent of x (radians).", math.tan)
_unary("asin(x)", "Arc sine in radians, for x in [-1, 1].", math.asin)
_unary("acos(x)", "Arc cosine in radians, for x in [-1, 1].", math.acos)
_unary("atan(x)", "Arc tangent in radians.", math.atan)
_unary("tanh(x)", "Hyperbolic tangent, in (-1, 1).", math.tanh)
_unary("erf(x)", "Error function; the standard normal CDF is (1 + $erf(x / $sqrt(2))) / 2.", math.erf)


@function("atan2(y, x)", "Angle in radians of the point (x, y), in (-pi, pi].", min_args=2, max_args=2)
def _atan2(call: Call) -> float:
    return math.atan2(number_arg(call, 0), number_arg(call, 1))


@function("hypot(x, y)", "Length of the vector (x, y): sqrt(x² + y²).", min_args=2, max_args=2)
def _hypot(call: Call) -> float:
    return _finite(call, math.hypot(number_arg(call, 0), number_arg(call, 1)))


@function("pow(x, y)", "x to the power y (like x ** y).", min_args=2, max_args=2)
def _power(call: Call) -> Any:
    try:
        return _pow(number_arg(call, 0), number_arg(call, 1), call.source)
    except ExprError as exc:
        raise fail(call, exc.detail) from None


@function("log_base(x, base)", "Logarithm of x in `base` (e.g. 2 or 10); $log(x) is the natural logarithm.",
          min_args=2, max_args=2)
def _log_base(call: Call) -> float:
    x = number_arg(call, 0)
    base = number_arg(call, 1)
    if x <= 0:
        raise fail(call, f"x must be above 0, got {x}")
    if base <= 0 or base == 1:
        raise fail(call, f"the base must be above 0 and not 1, got {base}")
    return math.log(x) / math.log(base)


@function("sigmoid(x)", "Logistic function 1 / (1 + e^-x), in (0, 1).", min_args=1, max_args=1)
def _sigmoid(call: Call) -> float:
    return sigmoid(number_arg(call, 0))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


@function("logit(p)", "Log-odds ln(p / (1 - p)) of a probability strictly between 0 and 1.", min_args=1, max_args=1)
def _logit(call: Call) -> float:
    p = number_arg(call, 0)
    if not 0 < p < 1:
        raise fail(call, f"p must be strictly between 0 and 1, got {p}")
    return math.log(p) - math.log1p(-p)


@function("sign(x)", "-1, 0 or 1 by the sign of x.", min_args=1, max_args=1)
def _sign(call: Call) -> int:
    x = number_arg(call, 0)
    return (x > 0) - (x < 0)


@function("lerp(a, b, t)", "The point a fraction `t` of the way from a to b: a + (b - a) × t (t is not clamped).",
          min_args=3, max_args=3)
def _lerp(call: Call) -> float:
    a, b, t = number_arg(call, 0), number_arg(call, 1), number_arg(call, 2)
    return _finite(call, a + (b - a) * t)


@function("interp(x, xs, ys)", "Piecewise-linear y at x through the points (xs, ys); xs strictly increasing; flat beyond the ends.",
          min_args=3, max_args=3)
def _interp(call: Call) -> float:
    x = number_arg(call, 0)
    xs = present_numbers(call, list_arg(call, 1, "a list of x points"), "the x points")
    ys = present_numbers(call, list_arg(call, 2, "a list of y points"), "the y points")
    if not xs or len(xs) != len(ys):
        raise fail(call, f"xs and ys must be non-empty lists of the same length (got {len(xs)} and {len(ys)}), without nulls")
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise fail(call, "xs must be strictly increasing")
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    hi = bisect.bisect_right(xs, x)
    lo = hi - 1
    t = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + (ys[hi] - ys[lo]) * t


def _whole_numbers(call: Call) -> List[int]:
    values = [int_arg(call, i, what="a whole number") for i in range(len(call))]
    for value in values:
        if abs(value).bit_length() > MAX_INT_BITS:
            raise fail(call, f"numbers are limited to {MAX_INT_BITS:,} bits")
    return values


@function("gcd(a, b, ...)", "Greatest common divisor of whole numbers (0 when all are 0).", min_args=2)
def _gcd(call: Call) -> int:
    return math.gcd(*_whole_numbers(call))


@function("lcm(a, b, ...)", "Least common multiple of whole numbers (0 when any is 0).", min_args=2)
def _lcm(call: Call) -> int:
    result = 1
    for value in _whole_numbers(call):
        result = math.lcm(result, value)
        if result.bit_length() > MAX_INT_BITS:
            raise fail(call, f"the result is past the limit of {MAX_INT_BITS:,} bits")
    return result


def _bits_of_log(log_value: float) -> float:
    return log_value / math.log(2)


@function("factorial(n)", "n! for a whole number n ≥ 0 (limited by the whole-number size limit).", min_args=1, max_args=1)
def _factorial(call: Call) -> int:
    n = int_arg(call, 0, low=0, what="a whole number n")
    if _bits_of_log(math.lgamma(n + 1)) > MAX_INT_BITS:
        raise fail(call, f"{n}! is past the limit of {MAX_INT_BITS:,} bits")
    charge(n, call.source)
    return math.factorial(n)


@function("comb(n, k)", "Ways to choose k of n items, ignoring order (0 when k > n).", min_args=2, max_args=2)
def _comb(call: Call) -> int:
    n = int_arg(call, 0, low=0, what="the number of items n")
    k = int_arg(call, 1, low=0, what="the number chosen k")
    if k > n:
        return 0
    if _bits_of_log(math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)) > MAX_INT_BITS:
        raise fail(call, f"comb({n}, {k}) is past the limit of {MAX_INT_BITS:,} bits")
    charge(min(k, n - k), call.source)
    return math.comb(n, k)


@function("pi()", "The constant pi (3.14159…).", max_args=0)
def _pi(call: Call) -> float:
    return math.pi


@function("e()", "The constant e (2.71828…).", max_args=0)
def _e(call: Call) -> float:
    return math.e


def _logits(call: Call) -> List[float]:
    values = list_arg(call, 0, "a list of numbers")
    numbers = present_numbers(call, values, "the scores")
    if len(numbers) != len(values):
        raise fail(call, "the scores cannot contain nulls")
    return [float(v) for v in numbers]


def logsumexp(values: List[float]) -> float:
    top = max(values)
    return top + math.log(math.fsum(math.exp(v - top) for v in values))


@function("softmax(scores, temperature?)", "Probabilities proportional to e^(score / temperature) (default temperature 1); sums to 1.",
          min_args=1, max_args=2)
def _softmax(call: Call) -> List[float]:
    scores = _logits(call)
    temperature = number_arg(call, 1, 1.0, what="the temperature")
    if temperature <= 0:
        raise fail(call, f"the temperature must be above 0, got {temperature}")
    if not scores:
        return []
    scaled = [s / temperature for s in scores]
    if not all(math.isfinite(s) for s in scaled):
        raise fail(call, "the temperature is too small for these scores")
    norm = logsumexp(scaled)
    return [math.exp(s - norm) for s in scaled]


@function("logsumexp(scores)", "ln(Σ e^score), computed without overflow; null for an empty list.", min_args=1, max_args=1)
def _logsumexp(call: Call) -> Any:
    scores = _logits(call)
    return logsumexp(scores) if scores else None
