"""Statistics: spread, association, inequality, information, time series and histograms.

Reductions skip nulls like the built-in aggregates (paired functions skip a pair when either side
is null). Series transforms keep positions, so a null in a series is an error.
"""
from __future__ import annotations

import math
from typing import Any

from ..expr import Call, charge, function
from ._args import check_len, fail, int_arg, list_arg, number_arg, present_numbers, series_arg

#: Most bins `$histogram` builds.
MAX_BINS = 10_000


def _values(call: Call, value_index: int, where_index: int | None = None) -> list[Any]:
    """Numbers from the items (or from the per-item `value` argument), nulls skipped."""
    items = call.filtered(0, where_index) if where_index is not None else call.collection(0)
    if len(call) <= value_index:
        return present_numbers(call, items)
    return present_numbers(call, [call.each(value_index, it, i) for i, it in enumerate(items)])


def _mean(values: list[Any]) -> float:
    return math.fsum(values) / len(values)


def _variance(values: list[Any]) -> float | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    return math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1)


@function("variance(items, value?, where?)",
          "Sample variance (n - 1) of `value` over matching items, or of a list; null when fewer than two.",
          min_args=1, max_args=3, lazy=[1, 2])
def _variance_fn(call: Call) -> float | None:
    return _variance(_values(call, 1, 2 if len(call) > 2 else None))


def _pairs(call: Call) -> tuple[list[Any], list[Any]]:
    xs, ys = list_arg(call, 0, "a list of x values"), list_arg(call, 1, "a list of y values")
    if len(xs) != len(ys):
        raise fail(call, f"xs and ys must be the same length, got {len(xs)} and {len(ys)}")
    kept = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    present_numbers(call, [v for pair in kept for v in pair])
    return [x for x, _ in kept], [y for _, y in kept]


def _moments(xs: list[Any], ys: list[Any]) -> tuple[float, float, float, float, float]:
    """Means and centred sums of squares and products: mx, my, sxx, syy, sxy."""
    mx, my = _mean(xs), _mean(ys)
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return mx, my, sxx, syy, sxy


@function("cov(xs, ys)",
          "Sample covariance of paired lists (pairs with a null skipped); null when fewer than two pairs.",
          min_args=2, max_args=2)
def _cov(call: Call) -> float | None:
    xs, ys = _pairs(call)
    if len(xs) < 2:
        return None
    return _moments(xs, ys)[4] / (len(xs) - 1)


@function("corr(xs, ys)",
          "Pearson correlation of paired lists, -1 to 1; null when fewer than two pairs or a list is constant.",
          min_args=2, max_args=2)
def _corr(call: Call) -> float | None:
    xs, ys = _pairs(call)
    if len(xs) < 2:
        return None
    _, _, sxx, syy, sxy = _moments(xs, ys)
    if sxx == 0 or syy == 0:
        return None
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


@function("linreg(xs, ys)",
          "Least-squares line through paired lists: {slope, intercept, r2, n}; null when fewer than two pairs or xs "
          "are constant.",
          min_args=2, max_args=2)
def _linreg(call: Call) -> dict[str, Any] | None:
    xs, ys = _pairs(call)
    if len(xs) < 2:
        return None
    mx, my, sxx, syy, sxy = _moments(xs, ys)
    if sxx == 0:
        return None
    slope = sxy / sxx
    intercept = my - slope * mx
    residual = math.fsum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 if syy == 0 else 1.0 - residual / syy
    return {"slope": slope, "intercept": intercept, "r2": r2, "n": len(xs)}


def _non_negative(call: Call, values: list[Any], what: str) -> list[Any]:
    if any(v < 0 for v in values):
        raise fail(call, f"{what} must be ≥ 0")
    return values


@function("gini(items, value?)",
          "Gini inequality of non-negative values, 0 (equal) to near 1 (one holds all); null when none.",
          min_args=1, max_args=2, lazy=[1])
def _gini(call: Call) -> float | None:
    values = sorted(_non_negative(call, _values(call, 1), "values"))
    if not values:
        return None
    total = math.fsum(values)
    if total == 0:
        return 0.0
    n = len(values)
    return math.fsum((2 * i - n - 1) * v for i, v in enumerate(values, 1)) / (n * total)


@function("hhi(items, value?)",
          "Herfindahl–Hirschman concentration: the sum of squared shares, 1/n (even) to 1 (one holds all); null when "
          "none.",
          min_args=1, max_args=2, lazy=[1])
def _hhi(call: Call) -> float | None:
    values = _non_negative(call, _values(call, 1), "values")
    total = math.fsum(values)
    if not values or total == 0:
        return None
    return math.fsum((v / total) ** 2 for v in values)


@function("entropy(weights, base?)",
          "Shannon entropy of probabilities or counts (a list or map; normalised), in bits unless `base` is given; "
          "null when all are 0.",
          min_args=1, max_args=2)
def _entropy(call: Call) -> float | None:
    raw = call.arg(0)
    values = (list(raw.values()) if isinstance(raw, dict)
              else list_arg(call, 0, "a list or map of probabilities or counts"))
    charge(len(values), call.source)
    weights = _non_negative(call, present_numbers(call, values, "weights"), "weights")
    base = number_arg(call, 1, 2.0, what="the logarithm base")
    if base <= 0 or base == 1:
        raise fail(call, f"the base must be above 0 and not 1, got {base}")
    total = math.fsum(weights)
    if total == 0:
        return None
    entropy = -math.fsum((w / total) * math.log(w / total) for w in weights if w > 0)
    return max(0.0, entropy / math.log(base))


@function("percentile_rank(values, x)",
          "Share of values below x, counting ties as half (0–1); nulls skipped, null when none.",
          min_args=2, max_args=2)
def _percentile_rank(call: Call) -> float | None:
    values = present_numbers(call, list_arg(call, 0, "a list of numbers"))
    x = number_arg(call, 1, what="the value x")
    if not values:
        return None
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return (below + 0.5 * equal) / len(values)


@function("zscore(x, values)",
          "Standard score (x - mean) / sample sd of the values; null when fewer than two values or no spread.",
          min_args=2, max_args=2)
def _zscore(call: Call) -> float | None:
    x = number_arg(call, 0, what="the value x")
    values = present_numbers(call, list_arg(call, 1, "a list of numbers"))
    variance = _variance(values)
    if not variance:
        return None
    return (x - _mean(values)) / math.sqrt(variance)


@function("autocorr(series, lag)",
          "Autocorrelation of a series with itself `lag` steps later; null when too short or constant.",
          min_args=2, max_args=2)
def _autocorr(call: Call) -> float | None:
    values = series_arg(call, 0)
    lag = int_arg(call, 1, low=0, what="the lag")
    if len(values) <= lag or len(values) < 2:
        return None
    mean = _mean(values)
    denominator = math.fsum((v - mean) ** 2 for v in values)
    if denominator == 0:
        return None
    numerator = math.fsum((values[t] - mean) * (values[t + lag] - mean) for t in range(len(values) - lag))
    return numerator / denominator


@function("returns(series, kind?)",
          "Step-to-step returns: simple (default) x[t] / x[t-1] - 1, or log ln(x[t] / x[t-1]); one shorter than the "
          "series.",
          min_args=1, max_args=2)
def _returns(call: Call) -> list[float]:
    values = series_arg(call, 0)
    kind = call.arg(1) if len(call) > 1 and call.arg(1) is not None else "simple"
    if kind not in ("simple", "log"):
        raise fail(call, f"kind must be simple or log, got {kind!r}")
    out = []
    for t, (previous, current) in enumerate(zip(values, values[1:]), 1):
        if previous == 0 or (kind == "log" and (previous < 0 or current <= 0)):
            raise fail(call, f"cannot take a {kind} return from {previous} to {current} (item {t})")
        out.append(current / previous - 1 if kind == "simple" else math.log(current / previous))
    return out


@function("ema(series, alpha)",
          "Exponential moving average, same length: e[0] = x[0], e[t] = alpha·x[t] + (1 - alpha)·e[t-1].",
          min_args=2, max_args=2)
def _ema(call: Call) -> list[float]:
    values = series_arg(call, 0)
    alpha = number_arg(call, 1, what="alpha")
    if not 0 < alpha <= 1:
        raise fail(call, f"alpha must be in (0, 1], got {alpha}")
    out: list[float] = []
    for value in values:
        out.append(float(value) if not out else alpha * value + (1 - alpha) * out[-1])
    return out


@function("sma(series, n)", "Simple moving average over the last n items, same length (null until n items exist).",
          min_args=2, max_args=2)
def _sma(call: Call) -> list[float | None]:
    values = series_arg(call, 0)
    n = int_arg(call, 1, low=1, what="the window length n")
    out: list[float | None] = []
    for t in range(len(values)):
        out.append(None if t + 1 < n else math.fsum(values[t + 1 - n:t + 1]) / n)
    charge(len(values) * min(n, len(values)), call.source)
    return out


@function("drawdown(series)",
          "Largest fall from a running peak as a fraction of that peak (0–1), e.g. 0.25 for 120 → 90.",
          min_args=1, max_args=1)
def _drawdown(call: Call) -> float:
    values = series_arg(call, 0)
    worst, peak = 0.0, None
    for value in values:
        if peak is None or value > peak:
            peak = value
        if peak <= 0:
            raise fail(call, f"a drawdown needs positive peaks, got {peak}")
        worst = max(worst, (peak - value) / peak)
    return worst


@function("histogram(values, bins, low?, high?)",
          "Counts in `bins` equal-width bins over [low, high] (default the data range): {edges, counts}; outside "
          "values and nulls skipped.",
          min_args=2, max_args=4)
def _histogram(call: Call) -> dict[str, list[Any]]:
    values = present_numbers(call, list_arg(call, 0, "a list of numbers"))
    bins = int_arg(call, 1, low=1, high=MAX_BINS, what="the number of bins")
    low = number_arg(call, 2, min(values) if values else 0.0, what="low")
    high = number_arg(call, 3, max(values) if values else 1.0, what="high")
    if high < low:
        raise fail(call, f"low {low} is above high {high}")
    if high == low:
        low, high = low - 0.5, high + 0.5
    check_len(call, bins, "bins")
    width = (high - low) / bins
    edges = [low + width * i for i in range(bins)] + [high]
    counts = [0] * bins
    for value in values:
        if low <= value <= high:
            counts[min(bins - 1, int((value - low) / width))] += 1
    return {"edges": edges, "counts": counts}
