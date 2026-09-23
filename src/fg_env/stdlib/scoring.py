"""Forecast scoring rules, ratings and forecast pooling."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ..expr import Call, _describe, charge, function
from ._args import fail, list_arg, number_arg, present_numbers, probability

#: How far a probability vector's sum may stray from 1.
SUM_TOLERANCE = 1e-6
#: Default probability floor for `$log_loss`, so a confident miss scores a large finite loss.
LOG_LOSS_EPSILON = 1e-15
#: Default K-factor for `$elo`.
ELO_K = 32
#: Default exponent for the extremized pool (> 1 pushes the pooled forecast away from even odds).
EXTREMIZE_EXPONENT = 2.5
POOL_METHODS = ("linear", "log", "extremized")


def _binary_outcome(call: Call, outcome: Any) -> int:
    if isinstance(outcome, bool):
        return int(outcome)
    if isinstance(outcome, (int, float)) and outcome in (0, 1):
        return int(outcome)
    raise fail(call, f"a binary outcome must be true/false or 1/0, got {_describe(outcome)}")


def _vector(call: Call, forecast: Any, what: str = "the forecast") -> Tuple[List[Any], List[float]]:
    """Keys and probabilities of a list or map forecast; probabilities in [0, 1] summing to 1."""
    if isinstance(forecast, dict):
        keys, raw = list(forecast), list(forecast.values())
    elif isinstance(forecast, (list, tuple)):
        keys, raw = list(range(len(forecast))), list(forecast)
    else:
        raise fail(call, f"{what} must be a probability, a list of probabilities or a map of them, got {_describe(forecast)}")
    charge(len(raw), call.source)
    if not raw:
        raise fail(call, f"{what} is empty")
    probabilities = [probability(call, p, f"each probability in {what}") for p in raw]
    if abs(math.fsum(probabilities) - 1) > SUM_TOLERANCE:
        raise fail(call, f"the probabilities in {what} must sum to 1, got {math.fsum(probabilities):.6g}")
    return keys, probabilities


def _outcome_position(call: Call, keys: List[Any], forecast: Any, outcome: Any) -> int:
    if isinstance(forecast, dict):
        if isinstance(outcome, str) and outcome in forecast:
            return keys.index(outcome)
        raise fail(call, f"the outcome must be one of the forecast's keys ({', '.join(map(str, keys))}), got {_describe(outcome)}")
    if isinstance(outcome, bool) or not isinstance(outcome, int) or not 0 <= outcome < len(keys):
        raise fail(call, f"the outcome must be a position 0..{len(keys) - 1} in the forecast list, got {_describe(outcome)}")
    return outcome


@function("brier(forecast, outcome)",
          "Brier score (lower is better): (p - outcome)² for a probability and a true/false outcome; for a list (outcome = position) or map (outcome = key), the sum over classes (0–2).",
          min_args=2, max_args=2)
def _brier(call: Call) -> float:
    forecast, outcome = call.arg(0), call.arg(1)
    if isinstance(forecast, (int, float)) and not isinstance(forecast, bool):
        return (probability(call, forecast, "the forecast") - _binary_outcome(call, outcome)) ** 2
    keys, probabilities = _vector(call, forecast)
    hit = _outcome_position(call, keys, forecast, outcome)
    return math.fsum((p - (1.0 if i == hit else 0.0)) ** 2 for i, p in enumerate(probabilities))


@function("log_loss(forecast, outcome, epsilon?)",
          "Log loss (lower is better): -ln of the probability given to what happened; probabilities are floored at `epsilon` (default 1e-15). Forecast forms as in $brier.",
          min_args=2, max_args=3)
def _log_loss(call: Call) -> float:
    forecast, outcome = call.arg(0), call.arg(1)
    epsilon = number_arg(call, 2, LOG_LOSS_EPSILON, what="epsilon")
    if not 0 < epsilon < 0.5:
        raise fail(call, f"epsilon must be between 0 and 0.5, got {epsilon}")
    if isinstance(forecast, (int, float)) and not isinstance(forecast, bool):
        p = probability(call, forecast, "the forecast")
        given = p if _binary_outcome(call, outcome) else 1 - p
    else:
        keys, probabilities = _vector(call, forecast)
        given = probabilities[_outcome_position(call, keys, forecast, outcome)]
    return -math.log(max(epsilon, given))


@function("crps(samples, outcome)",
          "Continuous ranked probability score of a sample forecast (list; nulls skipped) or a point forecast, against the outcome (lower is better; in the outcome's units).",
          min_args=2, max_args=2)
def _crps(call: Call) -> Optional[float]:
    outcome = number_arg(call, 1, what="the outcome")
    forecast = call.arg(0)
    if isinstance(forecast, (int, float)) and not isinstance(forecast, bool):
        return abs(number_arg(call, 0, what="the forecast") - outcome)
    samples = sorted(present_numbers(call, list_arg(call, 0, "a list of sample values or a number"), "samples"))
    if not samples:
        return None
    n = len(samples)
    spread_to_outcome = math.fsum(abs(x - outcome) for x in samples) / n
    # Mean |X - X'| over all n² ordered pairs, from the sorted samples in O(n log n).
    pairwise = 2 * math.fsum((2 * i - n - 1) * x for i, x in enumerate(samples, 1)) / (n * n)
    return spread_to_outcome - pairwise / 2


@function("abs_error(forecast, outcome)",
          "Absolute error |forecast - outcome|; for two equal-length lists, the mean absolute error (pairs with a null skipped; null when none).",
          min_args=2, max_args=2)
def _abs_error(call: Call) -> Optional[float]:
    forecast, outcome = call.arg(0), call.arg(1)
    if isinstance(forecast, (list, tuple)) or isinstance(outcome, (list, tuple)):
        predicted = list_arg(call, 0, "a list of forecasts")
        actual = list_arg(call, 1, "a list of outcomes")
        if len(predicted) != len(actual):
            raise fail(call, f"the lists must be the same length, got {len(predicted)} and {len(actual)}")
        pairs = [(f, o) for f, o in zip(predicted, actual) if f is not None and o is not None]
        present_numbers(call, [v for pair in pairs for v in pair])
        return math.fsum(abs(f - o) for f, o in pairs) / len(pairs) if pairs else None
    return abs(number_arg(call, 0, what="the forecast") - number_arg(call, 1, what="the outcome"))


@function("elo(rating_a, rating_b, score_a, k?)",
          "Elo update after a game: score_a is 1 (A won), 0.5 (draw) or 0 (A lost); gives {a, b, expected_a} with K-factor k (default 32).",
          min_args=3, max_args=4)
def _elo(call: Call) -> Dict[str, float]:
    a, b = number_arg(call, 0, what="rating_a"), number_arg(call, 1, what="rating_b")
    score = number_arg(call, 2, low=0, high=1, what="score_a")
    k = number_arg(call, 3, ELO_K, low=0, what="the K-factor")
    expected = 1.0 / (1.0 + 10 ** ((b - a) / 400))
    change = k * (score - expected)
    return {"a": a + change, "b": b - change, "expected_a": expected}


def _pool_weights(call: Call, count: int) -> List[float]:
    if len(call) < 4 or call.arg(3) is None:
        return [1.0 / count] * count
    weights = present_numbers(call, list_arg(call, 3, "a list of weights"), "weights")
    if len(weights) != count or any(w < 0 for w in weights) or math.fsum(weights) <= 0:
        raise fail(call, f"weights must be {count} numbers ≥ 0 (one per forecast, not all 0)")
    total = math.fsum(weights)
    return [w / total for w in weights]


def _pooled(call: Call, vectors: List[List[float]], weights: List[float], method: str, exponent: float) -> List[float]:
    size = len(vectors[0])
    if method == "linear":
        return [math.fsum(w * v[k] for w, v in zip(weights, vectors)) for k in range(size)]
    power = exponent if method == "extremized" else 1.0
    logs: List[float] = []
    for k in range(size):
        if any(v[k] == 0 and w > 0 for w, v in zip(weights, vectors)):
            logs.append(-math.inf)
        else:
            logs.append(power * math.fsum(w * math.log(v[k]) for w, v in zip(weights, vectors) if w > 0))
    top = max(logs)
    if top == -math.inf:
        raise fail(call, f"the {method} pool is undefined: every outcome was given probability 0 by some forecaster")
    unnormalised = [math.exp(x - top) if x != -math.inf else 0.0 for x in logs]
    total = math.fsum(unnormalised)
    return [u / total for u in unnormalised]


@function("pool(forecasts, method?, exponent?, weights?)",
          "Combine forecasts (probabilities, probability lists or maps): linear (weighted mean, default), log (normalised weighted geometric mean) or extremized (log pool raised to `exponent`, default 2.5).",
          min_args=1, max_args=4)
def _pool(call: Call) -> Any:
    forecasts = list_arg(call, 0, "a list of forecasts")
    if not forecasts:
        raise fail(call, "there are no forecasts to pool")
    method = call.arg(1) if len(call) > 1 and call.arg(1) is not None else "linear"
    if method not in POOL_METHODS:
        raise fail(call, f"method must be one of {', '.join(POOL_METHODS)}, got {_describe(method)}")
    has_exponent = len(call) > 2 and call.arg(2) is not None
    if has_exponent and method != "extremized":
        raise fail(call, "an exponent applies only to the extremized method")
    exponent = number_arg(call, 2, EXTREMIZE_EXPONENT, what="the exponent") if has_exponent else EXTREMIZE_EXPONENT
    if exponent <= 0:
        raise fail(call, f"the exponent must be above 0, got {exponent}")
    weights = _pool_weights(call, len(forecasts))
    first = forecasts[0]
    if all(isinstance(f, (int, float)) and not isinstance(f, bool) for f in forecasts):
        vectors = [[probability(call, f, "each forecast"), 1 - f] for f in forecasts]
        return _pooled(call, vectors, weights, method, exponent)[0]
    if isinstance(first, (list, tuple, dict)):
        keys, _ = _vector(call, first, "forecast 1")
        vectors = []
        for position, forecast in enumerate(forecasts, 1):
            if type(forecast) is not type(first) or (isinstance(first, dict) and list(forecast) != keys) or \
                    len(forecast) != len(keys):
                raise fail(call, f"forecast {position} does not have the same shape (and keys) as forecast 1")
            vectors.append(_vector(call, forecast, f"forecast {position}")[1])
        pooled = _pooled(call, vectors, weights, method, exponent)
        return dict(zip(keys, pooled)) if isinstance(first, dict) else pooled
    raise fail(call, "forecasts must all be probabilities, all probability lists, or all maps with the same keys")
