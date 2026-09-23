"""How each kind is estimated from rows — simple, documented estimators; what they do not estimate is assumed as declared.

| kind | estimated | method | assumed |
|---|---|---|---|
| trend | start, slope / rate / capacity, midpoint, steepness | least squares (log-linear for exponential; Nelder–Mead for logistic) | origin |
| seasonal | profile, or amplitude and peak, or harmonics | ratio of slot means to the overall mean (profile); harmonic regression | period, form |
| calendar | every effect | regression on the share of each round's days an effect matches (log scale for multiply) | which days match |
| elasticity | elasticity | log-log regression (constant); linear regression on price / reference (linear) | reference |
| promotion | lift | regression of log demand (exponential) or demand (linear) on promotion intensity | dip, retain |
| counts | dispersion | method of moments around `mean` or the `adjust` patterns (censored rows by their expected spread) | dist |
| random_walk | start, drift, sd | mean and spread of step changes (of log changes when multiplicative) | — |
| mean_reversion | mean, rate, sd | AR(1) regression, converted to the continuous-time rate | start |
| autoregressive | coefficients, mean, sd | least squares on lagged values | start |
| weather | mean, amplitude, peak, persistence, sd | harmonic regression on the year, AR(1) on what is left | — |
| draw / noise | the distribution's parameters | method of moments | — |
| carryover | retain | grid search: the retain whose carry-over best explains the value (least squares) | lag, form, start |
| saturation | limit, base and the curve's shape | least squares by Nelder–Mead | form |
| diffusion | p, q, market | least squares by Nelder–Mead on adopters (or new adopters) | start |
| hazard | rate, or shape and scale, or values | event share; maximum likelihood by Nelder–Mead; share per age | span |

A value column is divided by the patterns in ``adjust`` first (for counts, their product is the expected value).
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import timebase as tb
from .fit import Estimate, Problem, Row
from .numeric import dispersion, least_squares, nelder_mead
from .signals import _matches

__all__ = ["ESTIMATORS"]

Estimator = Callable[[Problem, Optional[str]], Estimate]
ESTIMATORS: Dict[str, Estimator] = {}
_PERIOD_SLOTS = {"year": 12, "week": 7, "day": 24}


def estimator(*kinds: str) -> Callable[[Estimator], Estimator]:
    def register(fn: Estimator) -> Estimator:
        for kind in kinds:
            ESTIMATORS[kind] = fn
        return fn
    return register


def _series(problem: Problem) -> List[Row]:
    rows = sorted((row for row in problem.rows if not row.censored), key=lambda row: row.t)
    if len(rows) < 3:
        raise ValueError(f"{len(rows)} usable row(s) is too few")
    return rows


def _values(problem: Problem, rows: Sequence[Row]) -> List[float]:
    return [row.y / problem.adjustment(row) for row in rows]


def _x(problem: Problem, row: Row) -> float:
    if "x" not in row.x:
        raise ValueError("this kind needs `fit.x`: the column holding its driver")
    return row.x["x"]


def _origin(problem: Problem) -> float:
    return tb.to_t(problem.clock, problem.current("origin", None)) if hasattr(problem.cfg, "origin") else 0.0


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------


@estimator("trend")
def _trend(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    tau = [row.t - _origin(problem) for row in rows]
    form = problem.cfg.form
    if form == "linear":
        fit = least_squares([[1.0, t] for t in tau], y)
        return Estimate({"start": fit.coef[0], "slope": fit.coef[1]}, {"start": fit.se[0], "slope": fit.se[1]},
                        "least squares", ["origin"], y, [fit.coef[0] + fit.coef[1] * t for t in tau])
    if form == "exponential":
        pairs = [(t, v) for t, v in zip(tau, y) if v > 0]
        fit = least_squares([[1.0, t] for t, _ in pairs], [math.log(v) for _, v in pairs])
        start = math.exp(fit.coef[0])
        return Estimate({"start": start, "rate": fit.coef[1]}, {"start": start * fit.se[0], "rate": fit.se[1]},
                        "least squares on log values", ["origin"], [v for _, v in pairs],
                        [start * math.exp(fit.coef[1] * t) for t, _ in pairs])
    span = (max(tau) - min(tau)) or 1.0

    def sse(p: List[float]) -> float:
        return sum((v - p[0] / (1 + math.exp(max(-700.0, min(700.0, -p[2] * (t - p[1])))))) ** 2 for t, v in zip(tau, y))

    best, _ = nelder_mead(sse, [max(y) * 1.05, statistics.median(tau), 8 / span], step=0.2)
    curve = [best[0] / (1 + math.exp(max(-700.0, min(700.0, -best[2] * (t - best[1]))))) for t in tau]
    return Estimate({"capacity": best[0], "midpoint": best[1], "steepness": best[2]}, {}, "least squares (Nelder–Mead)",
                    ["origin"], y, curve, ["no standard errors for a curve fitted by search"])


@estimator("seasonal")
def _seasonal(problem: Problem, key: Optional[str]) -> Estimate:
    cfg = problem.cfg
    rows = _series(problem)
    y = _values(problem, rows)
    multiply = cfg.form == "multiply"
    if cfg.profile is not None:
        declared = cfg.profile if isinstance(cfg.profile, list) else problem.current("profile", key)
        slots = len(declared) if isinstance(declared, list) and declared else _PERIOD_SLOTS.get(str(cfg.period))
        if not slots:
            raise ValueError("declare `profile` with one value per slot so fit knows how many to estimate")
        groups: List[List[float]] = [[] for _ in range(slots)]
        for row, value in zip(rows, y):
            groups[tb.slot(problem.clock, row.t, cfg.period, slots)].append(value)
        empty = [i for i, g in enumerate(groups) if not g]
        if empty:
            raise ValueError(f"slot(s) {empty} have no rows")
        overall = statistics.fmean(statistics.fmean(g) for g in groups)
        if multiply and overall <= 0:
            raise ValueError("a multiplicative profile needs values above 0")
        profile = [statistics.fmean(g) / overall if multiply else statistics.fmean(g) - overall for g in groups]
        errors = [(statistics.stdev(g) / math.sqrt(len(g)) / (overall if multiply else 1)) if len(g) > 1 else 0.0 for g in groups]
        base = overall
        predicted = [(base * profile[tb.slot(problem.clock, row.t, cfg.period, slots)]) if multiply
                     else base + profile[tb.slot(problem.clock, row.t, cfg.period, slots)] for row in rows]
        return Estimate({"profile": profile}, {"profile": errors}, "ratio of slot means to the overall mean"
                        if multiply else "slot means less the overall mean", ["period", "form"], y, predicted,
                        [f"level {base:.4g} (not part of the pattern)"])
    pairs = max(1, len(cfg.harmonics) if isinstance(cfg.harmonics, list) else 1)
    positions = [tb.position(problem.clock, row.t, cfg.period) for row in rows]
    design = [[1.0] + [f(2 * math.pi * k * p) for k in range(1, pairs + 1) for f in (math.sin, math.cos)] for p in positions]
    level = statistics.fmean(y)
    target = [v / level - 1 if multiply else v - level for v in y]
    fit = least_squares(design, target)
    coef = fit.coef[1:]
    predicted = [(level * (1 + sum(c * d for c, d in zip(fit.coef, row)))) if multiply
                 else level + sum(c * d for c, d in zip(fit.coef, row)) for row in design]
    if cfg.harmonics is None:
        a, b = coef[0], coef[1]
        amplitude = math.hypot(a, b)
        peak = (math.atan2(a, b) / (2 * math.pi)) % 1.0
        se = math.hypot(fit.se[1], fit.se[2]) / math.sqrt(2)
        return Estimate({"amplitude": amplitude, "peak": peak}, {"amplitude": se}, "harmonic regression",
                        ["period", "form"], y, predicted)
    harmonics = [[coef[2 * i], coef[2 * i + 1]] for i in range(pairs)]
    return Estimate({"harmonics": harmonics}, {}, "harmonic regression", ["period", "form"], y, predicted,
                    ["harmonic standard errors are not carried into runs"])


@estimator("calendar")
def _calendar(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    effects = problem.current("effects", key)
    multiply = problem.cfg.form == "multiply"
    design = []
    for row in rows:
        days = tb.days_covered(problem.clock, row.t)
        if not days:
            raise ValueError("calendar effects need clock.start and a calendar unit")
        design.append([1.0] + [sum(_matches(effect, day) for day in days) / len(days) for effect in effects])
    if multiply and any(v <= 0 for v in y):
        raise ValueError("multiplicative calendar effects need values above 0")
    fit = least_squares(design, [math.log(v) if multiply else v for v in y])
    values = [math.exp(c) if multiply else c for c in fit.coef[1:]]
    errors = [math.exp(c) * s if multiply else s for c, s in zip(fit.coef[1:], fit.se[1:])]
    fitted_effects = [{**effect, "effect": value} for effect, value in zip(effects, values)]
    predicted = [math.exp(sum(c * d for c, d in zip(fit.coef, row))) if multiply else sum(c * d for c, d in zip(fit.coef, row))
                 for row in design]
    notes = [f"{effect['on']}: ±{error:.3g}" for effect, error in zip(effects, errors)]
    return Estimate({"effects": fitted_effects}, {}, "regression on the share of days each effect matches",
                    ["which days each effect matches"], y, predicted, notes)


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------


@estimator("elasticity")
def _elasticity(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    reference = problem.current("reference", key)
    prices = [_x(problem, row) for row in rows]
    if problem.cfg.form == "constant":
        pairs = [(p, v) for p, v in zip(prices, y) if p > 0 and v > 0]
        fit = least_squares([[1.0, math.log(p / reference)] for p, _ in pairs], [math.log(v) for _, v in pairs])
        level = math.exp(fit.coef[0])
        return Estimate({"elasticity": fit.coef[1]}, {"elasticity": fit.se[1]}, "log-log regression", ["reference"],
                        [v for _, v in pairs], [level * (p / reference) ** fit.coef[1] for p, _ in pairs])
    fit = least_squares([[1.0, p / reference - 1] for p in prices], y)
    if fit.coef[0] <= 0:
        raise ValueError("demand at the reference price came out at or below 0")
    elasticity = fit.coef[1] / fit.coef[0]
    se = abs(elasticity) * math.hypot(fit.se[1] / fit.coef[1] if fit.coef[1] else 0.0, fit.se[0] / fit.coef[0])
    return Estimate({"elasticity": elasticity}, {"elasticity": se}, "linear regression on price / reference",
                    ["reference"], y, [fit.coef[0] + fit.coef[1] * (p / reference - 1) for p in prices])


@estimator("promotion")
def _promotion(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    depth = [_x(problem, row) for row in rows]
    if not any(d > 0 for d in depth) or all(d > 0 for d in depth):
        raise ValueError("promotion rows need both promoted and regular periods")
    if problem.cfg.form == "exponential":
        pairs = [(d, v) for d, v in zip(depth, y) if v > 0]
        fit = least_squares([[1.0, d] for d, _ in pairs], [math.log(v) for _, v in pairs])
        predicted = [math.exp(fit.coef[0] + fit.coef[1] * d) for d, _ in pairs]
        return Estimate({"lift": fit.coef[1]}, {"lift": fit.se[1]}, "regression of log demand on intensity",
                        ["dip", "retain"], [v for _, v in pairs], predicted)
    fit = least_squares([[1.0, d] for d in depth], y)
    lift = fit.coef[1] / fit.coef[0]
    return Estimate({"lift": lift}, {"lift": abs(lift) * fit.se[1] / abs(fit.coef[1]) if fit.coef[1] else 0.0},
                    "regression of demand on intensity", ["dip", "retain"], y, [fit.coef[0] + fit.coef[1] * d for d in depth])


@estimator("counts")
def _counts(problem: Problem, key: Optional[str]) -> Estimate:
    rows = problem.rows
    if problem.cfg.dist == "poisson":
        raise ValueError("a poisson pattern has nothing to estimate; use negative_binomial to estimate dispersion")
    means = [row.x["mean"] if "mean" in row.x else problem.adjustment(row) for row in rows]
    if not problem.cfg.fit.adjust and "mean" not in rows[0].x:
        raise ValueError("counts need the expected value: `fit.mean` (a column) or `fit.adjust` (patterns)")
    values = [row.y for row in rows]
    k = dispersion(values, means, [row.censored for row in rows])
    notes = [] if k is not None else ["no over-dispersion: the counts are no noisier than Poisson; dispersion set very large"]
    return Estimate({"dispersion": k if k is not None else 1e6}, {}, "method of moments", ["dist"], values, means, notes)


# ---------------------------------------------------------------------------
# random paths
# ---------------------------------------------------------------------------


def _steps(problem: Problem, rows: List[Row]) -> float:
    gaps = {round((b.t - a.t) / problem.clock.step, 9) for a, b in zip(rows, rows[1:])}
    if len(gaps) != 1 or 0 in gaps:
        raise ValueError("a random path is fitted from rows one step apart (sorted by time, no gaps)")
    return (rows[1].t - rows[0].t)


@estimator("random_walk")
def _random_walk(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    _steps(problem, rows)
    y = _values(problem, rows)
    multiply = problem.cfg.form == "multiply"
    if multiply and any(v <= 0 for v in y):
        raise ValueError("a multiplicative walk needs values above 0")
    changes = [math.log(b / a) if multiply else b - a for a, b in zip(y, y[1:])]
    drift, sd = statistics.fmean(changes), statistics.stdev(changes)
    predicted = [a * math.exp(drift) if multiply else a + drift for a in y[:-1]]
    return Estimate({"start": y[0], "drift": drift, "sd": sd}, {"drift": sd / math.sqrt(len(changes))},
                    "mean and spread of step changes", [], y[1:], predicted)


@estimator("mean_reversion")
def _mean_reversion(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    dt = _steps(problem, rows)
    y = _values(problem, rows)
    fit = least_squares([[1.0, a] for a in y[:-1]], y[1:])
    phi = fit.coef[1]
    if not 0 < phi < 1:
        raise ValueError(f"the values do not revert (lag-1 coefficient {phi:.3f} is not between 0 and 1)")
    rate = -math.log(phi) / dt
    mean = fit.coef[0] / (1 - phi)
    residual = fit.rmse * math.sqrt(len(y) - 1) / math.sqrt(len(y) - 3)
    sd = residual * math.sqrt(2 * rate / (1 - phi * phi))
    return Estimate({"mean": mean, "rate": rate, "sd": sd},
                    {"rate": fit.se[1] / phi / dt, "mean": fit.se[0] / (1 - phi)}, "AR(1) regression", ["start"],
                    y[1:], [fit.coef[0] + phi * a for a in y[:-1]])


@estimator("autoregressive")
def _autoregressive(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    _steps(problem, rows)
    y = _values(problem, rows)
    order = len(problem.cfg.coefficients) if isinstance(problem.cfg.coefficients, list) else 1
    design = [[1.0] + [y[i - j] for j in range(1, order + 1)] for i in range(order, len(y))]
    fit = least_squares(design, y[order:])
    phis = fit.coef[1:]
    if abs(sum(phis)) >= 1:
        raise ValueError("the coefficients add up to 1 or more: the series does not settle around a mean")
    mean = fit.coef[0] / (1 - sum(phis))
    predicted = [sum(c * d for c, d in zip(fit.coef, row)) for row in design]
    return Estimate({"coefficients": phis, "mean": mean, "sd": fit.rmse}, {"coefficients": fit.se[1:]},
                    "least squares on lagged values", ["start"], y[order:], predicted)


@estimator("weather")
def _weather(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    _steps(problem, rows)
    y = _values(problem, rows)
    positions = [tb.position(problem.clock, row.t, "year") for row in rows]
    design = [[1.0, math.sin(2 * math.pi * p), math.cos(2 * math.pi * p)] for p in positions]
    fit = least_squares(design, y)
    mean, a, b = fit.coef
    normal = [sum(c * d for c, d in zip(fit.coef, row)) for row in design]
    departures = [v - n for v, n in zip(y, normal)]
    ar = least_squares([[1.0, d] for d in departures[:-1]], departures[1:])
    persistence = min(0.999, max(0.0, ar.coef[1]))
    return Estimate({"mean": mean, "amplitude": math.hypot(a, b), "peak": (math.atan2(a, b) / (2 * math.pi)) % 1.0,
                     "persistence": persistence, "sd": statistics.pstdev(departures)},
                    {"mean": fit.se[0], "persistence": ar.se[1]}, "harmonic regression and AR(1) on departures", [],
                    y, normal)


# ---------------------------------------------------------------------------
# population
# ---------------------------------------------------------------------------


@estimator("draw", "noise")
def _draw(problem: Problem, key: Optional[str]) -> Estimate:
    values = [row.y for row in problem.rows if not row.censored]
    if len(values) < 3:
        raise ValueError(f"{len(values)} value(s) is too few")
    dist = getattr(problem.cfg, "dist", "normal")
    n, m, s = len(values), statistics.fmean(values), statistics.stdev(values)
    notes: List[str] = []
    if dist == "normal":
        params, errors = {"mean": m, "sd": s}, {"mean": s / math.sqrt(n)}
    elif dist == "lognormal":
        if min(values) <= 0:
            raise ValueError("lognormal values must be above 0")
        logs = [math.log(v) for v in values]
        key_mu = "mu" if problem.cfg.kind == "draw" else "mean"
        key_sigma = "sigma" if problem.cfg.kind == "draw" else "sd"
        params = {key_mu: statistics.fmean(logs), key_sigma: statistics.stdev(logs)}
        errors = {key_mu: statistics.stdev(logs) / math.sqrt(n)}
    elif dist == "gamma":
        params, errors = {"shape": m * m / (s * s), "scale": s * s / m}, {}
    elif dist == "beta":
        common = m * (1 - m) / (s * s) - 1
        if not 0 < m < 1 or common <= 0:
            raise ValueError("beta values must lie between 0 and 1 and vary less than a uniform")
        params, errors = {"a": m * common, "b": (1 - m) * common}, {}
    elif dist == "poisson":
        params, errors = {"mean": m}, {"mean": math.sqrt(m / n)}
    elif dist == "uniform":
        params, errors = {"low": min(values), "high": max(values)}, {}
        notes.append("the range seen, which understates the true range for small samples")
    else:
        raise ValueError(f"a {dist} draw cannot be fitted by moments")
    return Estimate(params, errors, "method of moments", [], values, [m] * n, notes)


@estimator("carryover")
def _carryover(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    drivers = [_x(problem, row) for row in rows]
    best: Optional[Tuple[float, float, Any, List[float]]] = None
    for step in range(0, 99):
        retain = step / 100
        stock, carried = 0.0, []
        for d in drivers:
            stock = d + retain * stock
            carried.append(stock)
        try:
            fit = least_squares([[1.0, c] for c in carried], y)
        except ValueError:
            continue
        if best is None or fit.r2 > best[1]:
            best = (retain, fit.r2, fit, carried)
    if best is None:
        raise ValueError("the driver never varies")
    retain, _, fit, carried = best
    return Estimate({"retain": retain}, {}, "grid search on retain (least squares)", ["lag", "form", "start"], y,
                    [fit.coef[0] + fit.coef[1] * c for c in carried],
                    [f"each unit of carry-over adds {fit.coef[1]:.4g} to the value (not part of the pattern)"])


@estimator("saturation")
def _saturation(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    xs = [_x(problem, row) for row in rows]
    form = problem.cfg.form
    top, low = max(y), min(y)

    def curve(p: List[float], x: float) -> float:
        base, limit = p[0], p[1]
        if form == "hill":
            half, shape = abs(p[2]) + 1e-12, abs(p[3]) + 1e-12
            return base + limit * (x ** shape / (half ** shape + x ** shape) if x > 0 else 0.0)
        if form == "logistic":
            return base + limit / (1 + math.exp(max(-700.0, min(700.0, -p[3] * (x - p[2])))))
        return base + limit * (1 - math.exp(-max(0.0, x) / (abs(p[2]) + 1e-12)))

    start = [low, top - low, statistics.median(xs) or 1.0] + ([1.0] if form != "exponential" else [])
    best, _ = nelder_mead(lambda p: sum((v - curve(p, x)) ** 2 for x, v in zip(xs, y)), start, step=0.3, iterations=4000)
    names = {"hill": ["base", "limit", "half", "shape"], "logistic": ["base", "limit", "midpoint", "steepness"],
             "exponential": ["base", "limit", "scale"]}[form]
    params = {name: (abs(v) if name in ("half", "shape", "scale") else v) for name, v in zip(names, best)}
    return Estimate(params, {}, "least squares (Nelder–Mead)", ["form"], y, [curve(best, x) for x in xs],
                    ["no standard errors for a curve fitted by search"])


@estimator("diffusion")
def _diffusion(problem: Problem, key: Optional[str]) -> Estimate:
    rows = _series(problem)
    y = _values(problem, rows)
    output = problem.cfg.output
    if output == "hazard":
        raise ValueError("fit the adopters over time (output adopters, new or share), not the hazard")
    start = tb.to_t(problem.clock, problem.current("start", key))
    step = problem.clock.step

    def share(p: float, q: float, tau: float) -> float:
        if tau <= 0:
            return 0.0
        decay = math.exp(-(p + q) * tau)
        return (1 - decay) / (1 + (q / p) * decay)

    def curve(params: List[float], t: float) -> float:
        p, q, market = abs(params[0]) + 1e-9, abs(params[1]), abs(params[2])
        tau = t - start
        if output == "share":
            return share(p, q, tau)
        if output == "adopters":
            return market * share(p, q, tau)
        return market * (share(p, q, tau + step) - share(p, q, tau))

    guess_market = 1.0 if output == "share" else (max(y) * 1.2 if output == "adopters" else sum(y) * 1.2)
    best, _ = nelder_mead(lambda params: sum((v - curve(params, row.t)) ** 2 for row, v in zip(rows, y)),
                          [0.02, 0.3, guess_market], step=0.4, iterations=6000)
    params = {"p": abs(best[0]), "q": abs(best[1])}
    if output != "share":
        params["market"] = abs(best[2])
    return Estimate(params, {}, "least squares (Nelder–Mead)", ["start"], y, [curve(best, row.t) for row in rows],
                    ["no standard errors for a curve fitted by search"])


@estimator("hazard")
def _hazard(problem: Problem, key: Optional[str]) -> Estimate:
    rows = problem.rows
    events = [1.0 if row.y else 0.0 for row in rows]
    ages = [_x(problem, row) for row in rows]
    form = problem.cfg.form
    n = len(rows)
    if n < 3:
        raise ValueError(f"{n} row(s) is too few")
    if form == "constant":
        rate = sum(events) / n
        return Estimate({"rate": rate}, {"rate": math.sqrt(rate * (1 - rate) / n)}, "share of rows with the event", ["span"],
                        events, [rate] * n)
    if form == "table":
        top = int(max(ages))
        shares: List[float] = []
        for age in range(top + 1):
            at = [e for a, e in zip(ages, events) if int(a) == age]
            shares.append(sum(at) / len(at) if at else (shares[-1] if shares else 0.0))
        return Estimate({"values": shares}, {}, "share with the event at each age", ["span"], events,
                        [shares[min(top, int(a))] for a in ages])
    span = problem.current("span", key)

    def chance(shape: float, scale: float, age: float) -> float:
        def survival(v: float) -> float:
            ratio = max(0.0, v) / scale
            return math.exp(-(ratio ** shape)) if form == "weibull" else 1 / (1 + ratio ** shape)
        now = survival(age)
        return min(1 - 1e-9, max(1e-9, 1 - survival(age + span) / now)) if now > 0 else 1 - 1e-9

    def loss(p: List[float]) -> float:
        shape, scale = abs(p[0]) + 1e-6, abs(p[1]) + 1e-6
        return -sum(e * math.log(chance(shape, scale, a)) + (1 - e) * math.log(1 - chance(shape, scale, a))
                    for a, e in zip(ages, events))

    best, _ = nelder_mead(loss, [1.0, statistics.fmean(ages) or 1.0], step=0.3, iterations=3000)
    shape, scale = abs(best[0]), abs(best[1])
    return Estimate({"shape": shape, "scale": scale}, {}, "maximum likelihood (Nelder–Mead)", ["span"], events,
                    [chance(shape, scale, a) for a in ages], ["no standard errors for a curve fitted by search"])
