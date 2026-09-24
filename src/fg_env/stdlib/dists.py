"""Random distributions. Every draw comes from the run's seeded generator, so runs replay exactly."""
from __future__ import annotations

import bisect
import math
import re
from statistics import NormalDist
from typing import Any

from ..expr import MAX_RANGE, Call, _describe, charge, function
from ..sampling.binomial import sample_large_binomial
from ._args import fail, int_arg, list_arg, number_arg, present_numbers, probability

#: Preserve geometric-skipping sequences through this expected count; larger draws use BTRS.
BINOMIAL_EXACT_MEAN = 1_000
#: Most dice one `$dice` roll may throw, and the most sides a die may have.
MAX_DICE = 10_000
MAX_SIDES = 1_000_000
_MAX_NOTATION = 200
_DICE_TERM = re.compile(r"\s*([+-])?\s*(?:(\d{0,6})[dD](\d{1,7})(?:(kh|kl)(\d{1,6}))?|(\d{1,9}))\s*")
_STANDARD = NormalDist()


def binomial(rng: Any, n: int, p: float) -> int:
    """Successes in n trials: geometric skipping for small counts, transformed rejection for large ones."""
    if n == 0 or p == 0:
        return 0
    if p == 1:
        return n
    q = min(p, 1 - p)
    if n * q > BINOMIAL_EXACT_MEAN:
        return sample_large_binomial(rng, n, p)
    log_miss = math.log1p(-q)
    successes, position = 0, 0
    while True:
        gap = math.log(1.0 - rng.random()) / log_miss
        # Tiny valid probabilities can make the floating gap infinite. It already
        # exceeds the remaining trials; never convert it to an integer first.
        if gap >= n - position:
            break
        position += int(gap) + 1
        successes += 1
    return successes if p <= 0.5 else n - successes


@function("binomial(n, p)", "Successes in n independent trials with success probability p.", min_args=2, max_args=2)
def _binomial(call: Call) -> int:
    n = int_arg(call, 0, low=0, high=10**12, what="the number of trials n")
    p = probability(call, call.arg(1), "p")
    charge(min(n, BINOMIAL_EXACT_MEAN), call.source)
    return binomial(call.rng, n, p)


@function("geometric(p)", "Trials up to and including the first success (1, 2, 3, …) with success probability p > 0.",
          min_args=1, max_args=1)
def _geometric(call: Call) -> int:
    p = probability(call, call.arg(0), "p")
    if p == 0:
        raise fail(call, "p must be above 0 (with p = 0 success never comes)")
    if p == 1:
        return 1
    return int(math.log(1.0 - call.rng.random()) / math.log1p(-p)) + 1


@function("gamma(shape, scale)", "Gamma-distributed number (mean shape × scale).", min_args=2, max_args=2)
def _gamma(call: Call) -> float:
    shape = number_arg(call, 0, what="the shape")
    scale = number_arg(call, 1, what="the scale")
    if shape <= 0 or scale <= 0:
        raise fail(call, f"shape and scale must be above 0, got {shape} and {scale}")
    return call.rng.gammavariate(shape, scale)


@function("weibull(scale, shape)", "Weibull-distributed number (shape < 1: early failures; > 1: wear-out).",
          min_args=2, max_args=2)
def _weibull(call: Call) -> float:
    scale = number_arg(call, 0, what="the scale")
    shape = number_arg(call, 1, what="the shape")
    if shape <= 0 or scale <= 0:
        raise fail(call, f"scale and shape must be above 0, got {scale} and {shape}")
    return call.rng.weibullvariate(scale, shape)


@function("triangular(low, high, mode)", "Number between low and high, most likely near `mode`.", min_args=3,
          max_args=3)
def _triangular(call: Call) -> float:
    low, high, mode = number_arg(call, 0), number_arg(call, 1), number_arg(call, 2)
    if not low <= mode <= high:
        raise fail(call, f"needs low ≤ mode ≤ high, got {low}, {mode}, {high}")
    if low == high:
        return float(low)
    return call.rng.triangular(low, high, mode)


def _weights(call: Call, index: int, what: str) -> tuple[list[Any], list[float]]:
    """Keys (positions for a list) and non-negative weights from a list or map; at least one above 0."""
    value = call.arg(index)
    if isinstance(value, dict):
        keys, raw = list(value), list(value.values())
        charge(len(raw), call.source)
    else:
        raw = list_arg(call, index, f"{what} (a list or map of numbers)")
        keys = list(range(len(raw)))
    numbers = present_numbers(call, raw, what)
    if len(numbers) != len(raw) or any(w < 0 for w in numbers) or not numbers:
        raise fail(call, f"{what} must be a non-empty list or map of numbers ≥ 0, without nulls")
    return keys, [float(w) for w in numbers]


@function("dirichlet(alphas)",
          "Random probabilities (summing to 1) from a Dirichlet with concentration `alphas` (each > 0).",
          min_args=1, max_args=1)
def _dirichlet(call: Call) -> list[float]:
    _, alphas = _weights(call, 0, "the alphas")
    if any(a <= 0 for a in alphas):
        raise fail(call, "every alpha must be above 0")
    rng = call.rng
    draws = [rng.gammavariate(a, 1.0) for a in alphas]
    total = math.fsum(draws)
    if total == 0:  # every draw underflowed (tiny alphas): all mass lands on one category
        winner = rng.choices(range(len(alphas)), weights=alphas, k=1)[0]
        return [1.0 if i == winner else 0.0 for i in range(len(alphas))]
    return [d / total for d in draws]


@function("multinomial(n, weights)",
          "n draws split across categories in proportion to `weights` (list → list of counts, map → map of counts).",
          min_args=2, max_args=2)
def _multinomial(call: Call) -> Any:
    n = int_arg(call, 0, low=0, high=10**12, what="the number of draws n")
    keys, weights = _weights(call, 1, "the weights")
    largest = max(weights)
    if largest <= 0:
        raise fail(call, "at least one weight must be above 0")
    # Relative weights must not overflow merely because of their chosen units.
    weights = [weight / largest for weight in weights]
    last = max(i for i, weight in enumerate(weights) if weight > 0)
    # Sum suffixes directly: subtracting a large category from the total can
    # erase the smaller categories or distort their conditional probabilities.
    remaining = [0.0] * len(weights)
    total = correction = 0.0
    for index in range(len(weights) - 1, -1, -1):
        weight = weights[index]
        combined = total + weight
        correction += (total - combined) + weight if total >= weight else (weight - combined) + total
        total = combined
        remaining[index] = total + correction
    counts: list[int] = []
    left = n
    for index, weight in enumerate(weights):
        if left == 0 or weight == 0:
            counts.append(0)
        else:
            share = 1.0 if index == last else min(1.0, weight / remaining[index])
            charge(min(left, BINOMIAL_EXACT_MEAN), call.source)
            drawn = binomial(call.rng, left, share)
            counts.append(drawn)
            left -= drawn
    if isinstance(call.arg(1), dict):
        return dict(zip(keys, counts))
    return counts


@function("zipf(n, s)", "Whole number 1..n with probability proportional to 1 / k^s (rank 1 most likely).",
          min_args=2, max_args=2)
def _zipf(call: Call) -> int:
    n = int_arg(call, 0, low=1, high=MAX_RANGE, what="the number of ranks n")
    s = number_arg(call, 1, low=0, what="the exponent s")
    charge(n, call.source)
    cumulative: list[float] = []
    total = 0.0
    for k in range(1, n + 1):
        total += k ** -s
        cumulative.append(total)
    return min(n, bisect.bisect_right(cumulative, call.rng.random() * total) + 1)


def _upper_tail(rng: Any, a: float, b: float) -> float:
    """A standard normal draw restricted to [a, b] with a ≥ 0, by inverting the survival function."""
    upper, lower = _STANDARD.cdf(-a), _STANDARD.cdf(-b)  # P(Z > a), P(Z > b), accurate far into the tail
    mass = upper - lower
    if upper > 0 and mass > 0:
        u = lower + rng.random() * mass
        if 0 < u < 1:
            return min(b, max(a, -_STANDARD.inv_cdf(u)))
    # Beyond double precision the tail is exponential to first order: Z ≈ a + Exp(1) / a.
    return min(b, a + rng.expovariate(1.0) / max(a, 1e-300))


@function("truncnormal(mean, sd, low, high)", "Normal number restricted to [low, high] (exact inversion; one draw).",
          min_args=4, max_args=4)
def _truncnormal(call: Call) -> float:
    mean, sd = number_arg(call, 0, what="the mean"), number_arg(call, 1, what="the sd")
    low, high = number_arg(call, 2, what="low"), number_arg(call, 3, what="high")
    if sd <= 0:
        raise fail(call, f"sd must be above 0, got {sd}")
    if low > high:
        raise fail(call, f"low {low} is above high {high}")
    if low == high:
        return float(low)
    a, b = (low - mean) / sd, (high - mean) / sd
    rng = call.rng
    if a >= 0:
        z = _upper_tail(rng, a, b)
    elif b <= 0:
        z = -_upper_tail(rng, -b, -a)
    else:
        lo, hi = _STANDARD.cdf(a), _STANDARD.cdf(b)
        u = lo + rng.random() * (hi - lo)
        z = _STANDARD.inv_cdf(u) if 0 < u < 1 else (a if u <= 0 else b)
    return min(high, max(low, mean + sd * z))


def parse_dice(notation: str) -> list[tuple[int, int, int, str, int]]:
    """``"3d6+2"`` → terms ``(sign, count, sides, keep, kept)``; a constant term has ``sides`` 0.
    Raises ``ValueError`` describing the problem."""
    if not notation.strip():
        raise ValueError("the notation is empty; write something like 3d6+2")
    terms: list[tuple[int, int, int, str, int]] = []
    position = 0
    while position < len(notation):
        match = _DICE_TERM.match(notation, position)
        if match is None or match.end() == position:
            raise ValueError(f"cannot read '{notation[position:]}'; write terms like 2d6, d20, 4d6kh3 or 5 joined by + "
                             "or -")
        sign_text, count_text, sides_text, keep, kept_text, constant = match.groups()
        if terms and sign_text is None:
            raise ValueError(f"terms must be joined by + or - near '{match.group(0).strip()}'")
        sign = -1 if sign_text == "-" else 1
        if constant is not None:
            terms.append((sign, int(constant), 0, "", 0))
        else:
            count = int(count_text) if count_text else 1
            sides = int(sides_text)
            kept = int(kept_text) if kept_text else count
            if not 1 <= sides <= MAX_SIDES:
                raise ValueError(f"dice need 1 to {MAX_SIDES:,} sides, got {sides}")
            if keep and not 1 <= kept <= count:
                raise ValueError(f"cannot keep {kept} of {count} dice")
            terms.append((sign, count, sides, keep or "", kept))
        position = match.end()
    if sum(t[1] for t in terms if t[2]) > MAX_DICE:
        raise ValueError(f"at most {MAX_DICE:,} dice per roll")
    return terms


@function("dice(notation)",
          "Roll dice written like 3d6+2, d20, 2d8-1 or 4d6kh3 (keep highest 3; kl keeps lowest) and return the total.",
          min_args=1, max_args=1)
def _dice(call: Call) -> int:
    notation = call.arg(0)
    if not isinstance(notation, str) or len(notation) > _MAX_NOTATION:
        raise fail(call,
                   f"pass dice notation text (up to {_MAX_NOTATION} characters) like '3d6+2', got "
                   f"{_describe(notation)}")
    try:
        terms = parse_dice(notation)
    except ValueError as exc:
        raise fail(call, str(exc)) from None
    rng = call.rng
    total = 0
    for sign, count, sides, keep, kept in terms:
        if not sides:
            total += sign * count
            continue
        charge(count, call.source)
        rolls = [rng.randint(1, sides) for _ in range(count)]
        if keep:
            rolls = sorted(rolls, reverse=(keep == "kh"))[:kept]
        total += sign * sum(rolls)
    return total
