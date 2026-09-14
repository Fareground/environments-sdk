"""Market analytics: returns, volatility, stylized facts and a realism score.

The realism score compares a simulated tape with a reference on the stylized facts every real
market shows — volatility level, fat tails, no memory in returns, volatility clustering and
volume that tracks volatility — as in the Fareground Exchange calibration. Every component is a
0..1 score shown with both values, so the overall number is auditable.

Expression functions: ``$realized_vol``, ``$excess_kurtosis``,
``$vol_clustering``, ``$volume_vol_corr``, ``$market_stats`` and ``$market_realism``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..expr import Call, ExprError, function

__all__ = ["log_returns", "stdev", "autocorr", "correlation", "excess_kurtosis", "vol_clustering",
           "series_stats", "realism_score"]

#: Lags averaged for volatility clustering (autocorrelation of absolute returns).
CLUSTER_LAGS = (1, 2, 3, 4, 5)


def log_returns(prices: Sequence[float]) -> List[float]:
    """Log returns between consecutive positive prices."""
    out: List[float] = []
    for prev, cur in zip(prices, prices[1:]):
        if prev is not None and cur is not None and prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: Sequence[float]) -> float:
    """Sample standard deviation; 0 with fewer than two values."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def autocorr(xs: Sequence[float], lag: int = 1) -> float:
    n = len(xs)
    if lag < 1 or n <= lag + 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs)
    if var <= 0:
        return 0.0
    return sum((xs[i] - m) * (xs[i + lag] - m) for i in range(n - lag)) / var


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = list(xs[:n]), list(ys[:n])
    mx, my = _mean(xs), _mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx <= 0 or sy <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def excess_kurtosis(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 4:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / n
    if var <= 0:
        return 0.0
    return (sum((x - m) ** 4 for x in xs) / n) / (var * var) - 3.0


def vol_clustering(returns: Sequence[float], lags: Sequence[int] = CLUSTER_LAGS) -> float:
    """Mean autocorrelation of absolute returns over ``lags``: positive when big moves cluster."""
    absolute = [abs(r) for r in returns]
    return _mean([autocorr(absolute, lag) for lag in lags])


def max_drawdown(prices: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for p in prices:
        peak = max(peak, p)
        if peak > 0:
            worst = min(worst, p / peak - 1.0)
    return worst


def series_stats(prices: Sequence[float], volumes: Optional[Sequence[float]] = None) -> Dict[str, float]:
    """Stylized-fact summary of one price series (one value per bar, oldest first)."""
    rets = log_returns(prices)
    stats: Dict[str, float] = {
        "bars": len(prices),
        "sigma": stdev(rets),
        "mean_return": _mean(rets),
        "kurtosis": excess_kurtosis(rets),
        "acf1": autocorr(rets, 1),
        "acf_abs": vol_clustering(rets),
        "max_drawdown": max_drawdown(prices),
        "total_return": (prices[-1] / prices[0] - 1.0) if len(prices) >= 2 and prices[0] > 0 else 0.0,
    }
    if volumes is not None and len(volumes) >= 2:
        vols = [float(v) for v in volumes]
        stats["avg_volume"] = _mean(vols)
        # Volume of bar t against the absolute return into bar t.
        stats["vol_volume_corr"] = correlation([abs(r) for r in rets], vols[len(vols) - len(rets):])
    return stats


def _closeness(sim: float, ref: float, tolerance: float) -> float:
    return max(0.0, 1.0 - abs(sim - ref) / tolerance) if tolerance > 0 else 0.0


def _ratio_score(sim: float, ref: float) -> float:
    if sim <= 0 or ref <= 0:
        return 0.0
    return math.exp(-abs(math.log(sim / ref)))


def realism_score(sim: Mapping[str, float], ref: Mapping[str, float]) -> Dict[str, Any]:
    """Compare simulated stylized facts with a reference: ``{score, components}`` (each 0..1)."""
    components: List[Dict[str, Any]] = []

    def add(key: str, label: str, score: float, sim_value: float, ref_value: Optional[float], note: str) -> None:
        components.append({"key": key, "label": label, "score": round(max(0.0, min(1.0, score)), 3),
                           "sim": round(sim_value, 5), "ref": None if ref_value is None else round(ref_value, 5),
                           "note": note})

    add("volatility", "Volatility", _ratio_score(sim.get("sigma", 0.0), ref.get("sigma", 0.0)),
        sim.get("sigma", 0.0), ref.get("sigma", 0.0), "per-bar return standard deviation")
    sim_k, ref_k = sim.get("kurtosis", 0.0), ref.get("kurtosis", 0.0)
    add("fat_tails", "Fat tails", 1.0 if sim_k > 0.3 and ref_k > 0.3 else _closeness(sim_k, ref_k, 3.0),
        sim_k, ref_k, "excess kurtosis; real returns have heavier tails than a normal")
    add("no_return_memory", "No return memory", 1.0 - min(1.0, abs(sim.get("acf1", 0.0)) * 4.0),
        sim.get("acf1", 0.0), ref.get("acf1", 0.0), "lag-1 autocorrelation of returns should sit near zero")
    sim_a, ref_a = sim.get("acf_abs", 0.0), ref.get("acf_abs", 0.0)
    add("volatility_clustering", "Volatility clustering",
        min(1.0, max(0.0, sim_a) / max(ref_a, 0.05)) if sim_a > 0 else 0.0, sim_a, ref_a,
        "autocorrelation of absolute returns should be positive and persistent")
    if "avg_volume" in sim and "avg_volume" in ref and ref["avg_volume"] > 0:
        add("volume", "Volume", _ratio_score(sim["avg_volume"], ref["avg_volume"]), sim["avg_volume"],
            ref["avg_volume"], "average traded quantity per bar")
    if "vol_volume_corr" in sim and "vol_volume_corr" in ref:
        add("volume_volatility", "Volume tracks volatility",
            1.0 if sim["vol_volume_corr"] > 0.1 else max(0.0, sim["vol_volume_corr"] / 0.1),
            sim["vol_volume_corr"], ref["vol_volume_corr"], "big moves should come with big volume")
    overall = _mean([c["score"] for c in components]) if components else 0.0
    return {"score": round(overall, 3), "components": components}


# ---------------------------------------------------------------------------
# Expression functions
# ---------------------------------------------------------------------------


def _numbers(call: Call, index: int, what: str) -> List[float]:
    value = call.arg(index)
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ExprError(f"${call.name}: {what} must be a list of numbers, got {value!r}", call.source)
    out: List[float] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ExprError(f"${call.name}: {what} must hold numbers, got {item!r}", call.source)
        out.append(float(item))
    return out


def _stats_of(call: Call, value: Any, what: str) -> Dict[str, float]:
    """A price list, ``{prices, volumes}``, or a stats map already computed."""
    if isinstance(value, Mapping):
        if "sigma" in value:
            return {k: float(v) for k, v in value.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        prices, volumes = value.get("prices"), value.get("volumes")
        if not isinstance(prices, (list, tuple)):
            raise ExprError(f"${call.name}: {what} as a map needs `prices` (and optionally `volumes`) or stats "
                            "from $market_stats", call.source)
        return series_stats([float(p) for p in prices if p is not None],
                            [float(v) for v in volumes if v is not None] if isinstance(volumes, (list, tuple)) else None)
    if isinstance(value, (list, tuple)):
        return series_stats([float(p) for p in value if isinstance(p, (int, float)) and not isinstance(p, bool)])
    raise ExprError(f"${call.name}: {what} must be a price list, {{prices, volumes}} or $market_stats(...), got {value!r}",
                    call.source)


@function("realized_vol(prices)", "Realized volatility: the standard deviation of log returns of a price series.",
          min_args=1, max_args=1)
def _realized_vol_function(call: Call) -> float:
    return stdev(log_returns(_numbers(call, 0, "prices")))


@function("excess_kurtosis(values)", "Excess kurtosis of a series (0 for a normal; positive = fat tails).",
          min_args=1, max_args=1)
def _kurtosis_function(call: Call) -> float:
    return excess_kurtosis(_numbers(call, 0, "values"))


@function("vol_clustering(prices)", "Volatility clustering: mean autocorrelation of absolute log returns at lags 1-5.",
          min_args=1, max_args=1)
def _clustering_function(call: Call) -> float:
    return vol_clustering(log_returns(_numbers(call, 0, "prices")))


@function("volume_vol_corr(prices, volumes)", "Correlation of each bar's volume with its absolute log return.",
          min_args=2, max_args=2)
def _volume_vol_function(call: Call) -> float:
    return series_stats(_numbers(call, 0, "prices"), _numbers(call, 1, "volumes")).get("vol_volume_corr", 0.0)


@function("market_stats(prices, volumes?)", "Stylized facts of a price series: {bars, sigma, mean_return, kurtosis, "
          "acf1, acf_abs, max_drawdown, total_return, avg_volume, vol_volume_corr}.", min_args=1, max_args=2)
def _stats_function(call: Call) -> Dict[str, float]:
    volumes = _numbers(call, 1, "volumes") if len(call) > 1 else None
    return series_stats(_numbers(call, 0, "prices"), volumes)


@function("market_realism(series, reference)", "Realism score of a simulated tape against a reference: {score, "
          "components} on volatility, fat tails, no return memory, volatility clustering, volume and volume-volatility "
          "correlation. Each side is a price list, {prices, volumes} or $market_stats(...).", min_args=2, max_args=2)
def _realism_function(call: Call) -> Dict[str, Any]:
    return realism_score(_stats_of(call, call.arg(0), "series"), _stats_of(call, call.arg(1), "reference"))
