"""Named statistics of one series: the stylized facts a calibration target can name.

A statistic is written ``"name"`` or ``"name:argument"`` (``"autocorrelation:2"``). Return
statistics (``volatility``, ``kurtosis`` …) use log returns and need a positive series.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from .stats import mean, sd

__all__ = ["STATISTICS", "statistic", "log_returns", "autocorrelation", "excess_kurtosis", "skewness",
           "max_drawdown", "describe_statistics"]

#: Lags averaged for volatility clustering (the autocorrelation of absolute returns).
_CLUSTERING_LAGS = 5


def log_returns(series: Sequence[float]) -> list[float]:
    if any(v <= 0 for v in series):
        raise ValueError("return statistics need a positive series (prices, levels); this one has values ≤ 0")
    return [math.log(b / a) for a, b in zip(series, series[1:])]


def autocorrelation(series: Sequence[float], lag: int = 1) -> float:
    """Sample autocorrelation at ``lag`` (0 when the series is flat or too short)."""
    if lag < 1:
        raise ValueError("lag must be at least 1")
    n = len(series)
    if n <= lag + 1:
        return 0.0
    m = mean(series)
    variance = math.fsum((v - m) ** 2 for v in series)
    if variance <= 0:
        return 0.0
    return math.fsum((series[i] - m) * (series[i + lag] - m) for i in range(n - lag)) / variance


def excess_kurtosis(values: Sequence[float]) -> float:
    """Population excess kurtosis: 0 for a normal, positive for fat tails."""
    n = len(values)
    if n < 4:
        return 0.0
    m = mean(values)
    variance = math.fsum((v - m) ** 2 for v in values) / n
    if variance <= 0:
        return 0.0
    return math.fsum((v - m) ** 4 for v in values) / n / variance ** 2 - 3.0


def skewness(values: Sequence[float]) -> float:
    n = len(values)
    if n < 3:
        return 0.0
    m = mean(values)
    variance = math.fsum((v - m) ** 2 for v in values) / n
    if variance <= 0:
        return 0.0
    return math.fsum((v - m) ** 3 for v in values) / n / variance ** 1.5


def max_drawdown(series: Sequence[float]) -> float:
    """Largest fall from a running peak, as a positive fraction of that peak."""
    peak, worst = -math.inf, 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1.0 - v / peak)
    return worst


def _trend(series: Sequence[float]) -> float:
    """Least-squares slope per round."""
    n = len(series)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2.0
    ym = mean(series)
    return math.fsum((i - xm) * (v - ym) for i, v in enumerate(series)) / math.fsum((i - xm) ** 2 for i in range(n))


def _clustering(series: Sequence[float]) -> float:
    absolute = [abs(r) for r in log_returns(series)]
    return mean([autocorrelation(absolute, lag) for lag in range(1, _CLUSTERING_LAGS + 1)])


#: name → (function(series, argument), argument default, plain description)
STATISTICS: dict[str, tuple[Callable[[Sequence[float], int], float], int, str]] = {
    "mean": (lambda s, a: mean(s), 0, "average value"),
    "sd": (lambda s, a: sd(s), 0, "standard deviation of the values"),
    "min": (lambda s, a: min(s), 0, "smallest value"),
    "max": (lambda s, a: max(s), 0, "largest value"),
    "first": (lambda s, a: s[0], 0, "first value"),
    "final": (lambda s, a: s[-1], 0, "last value"),
    "range": (lambda s, a: max(s) - min(s), 0, "largest minus smallest value"),
    "change": (lambda s, a: s[-1] - s[0], 0, "last minus first value"),
    "trend": (lambda s, a: _trend(s), 0, "least-squares slope per round"),
    "time_to_peak": (lambda s, a: float(max(range(len(s)), key=lambda i: s[i]) + 1), 0,
                     "round (1-based) of the largest value"),
    "autocorrelation": (lambda s, a: autocorrelation(s, a), 1, "autocorrelation of the values at a lag (default 1)"),
    "volatility": (lambda s, a: sd(log_returns(s)), 0, "standard deviation of log returns"),
    "return_autocorrelation": (lambda s, a: autocorrelation(log_returns(s), a), 1,
                               "autocorrelation of log returns at a lag (≈0 in efficient markets)"),
    "volatility_clustering": (lambda s, a: _clustering(s), 0,
                              f"mean autocorrelation of absolute log returns over lags 1-{_CLUSTERING_LAGS}"),
    "kurtosis": (lambda s, a: excess_kurtosis(log_returns(s)), 0, "excess kurtosis of log returns (fat tails > 0)"),
    "skew": (lambda s, a: skewness(log_returns(s)), 0, "skewness of log returns"),
    "max_drawdown": (lambda s, a: max_drawdown(s), 0, "largest fall from a running peak, as a fraction"),
}


def statistic(name: str, series: Sequence[float]) -> float:
    """Evaluate a named statistic (``"volatility"``, ``"autocorrelation:2"``) on a series."""
    key, _, argument = name.partition(":")
    if key not in STATISTICS:
        raise ValueError(f"unknown statistic '{key}' (known: {', '.join(STATISTICS)})")
    function, default, _ = STATISTICS[key]
    if argument:
        try:
            lag = int(argument)
        except ValueError:
            raise ValueError(f"statistic '{name}': the argument after ':' must be a whole number") from None
    else:
        lag = default
    values = [float(v) for v in series]
    if not values:
        raise ValueError(f"statistic '{name}' of an empty series")
    return float(function(values, lag))


def describe_statistics() -> str:
    return "\n".join(f"{name}: {spec[2]}" for name, spec in STATISTICS.items())
