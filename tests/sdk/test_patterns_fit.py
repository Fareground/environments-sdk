"""Fitting patterns from data recovers the parameters that generated it, writes them back as inputs with their
standard errors, and the fitted world can be decomposed and described."""
import copy
import csv
import math
import random
import statistics

import pytest

import fg_env

from patterns_helpers import world

WEEKS = {"unit": "week", "start": "2020-01-06"}


def _fitted(patterns, rows, *, clock=None, rounds=10, **sections):
    contract = world(patterns, rounds=rounds, clock=clock,
                     inputs={"history": {"type": "table", "default": rows}, **sections.pop("inputs", {})}, **sections)
    return fg_env.fit_patterns(contract)


def _param(result, pattern, field):
    return result.contract["inputs"][f"{pattern}_{field}"]["default"]


def _negbin(rng, mean, k):
    lam = rng.gammavariate(k, mean / k)
    count, limit, product = 0, math.exp(-lam), rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def test_a_linear_trend_is_recovered_with_a_standard_error_that_covers_the_truth():
    rng = random.Random(1)
    rows = [{"t": t, "y": 5 + 0.3 * t + rng.gauss(0, 1)} for t in range(200)]
    result = _fitted({"g": {"kind": "trend", "fit": {"data": "$inputs.history", "value": "y", "time": "t"}}}, rows)
    slope, se = _param(result, "g", "slope"), result.contract["inputs"]["g_slope_se"]["default"]
    assert abs(slope - 0.3) < 3 * se and se < 0.005
    assert result.fits[0].r2 > 0.99 and "least squares" in result.fits[0].method


def test_an_exponential_trend_and_a_monthly_profile_are_recovered_from_weekly_history():
    rng = random.Random(2)
    profile = [0.8, 0.85, 1.0, 1.1, 1.2, 1.15, 1.05, 1.0, 0.95, 0.95, 0.9, 1.05]
    mean = sum(profile) / 12
    profile = [p / mean for p in profile]
    clock = {"rounds": 208, **WEEKS}
    probe = fg_env.load(world({"s": {"kind": "seasonal", "profile": profile}}, clock=clock, rounds=208), seed=0)
    rows = []
    for week in range(208):
        month = probe.world.patterns.evaluate("s", None, [], float(week), "test")
        rows.append({"t": week, "y": 100 * math.exp(0.004 * week) * month * math.exp(rng.gauss(0, 0.02))})
    result = _fitted({"growth": {"kind": "trend", "form": "exponential",
                                 "fit": {"data": "$inputs.history", "value": "y", "time": "t"}},
                      "season": {"kind": "seasonal", "profile": [1] * 12,
                                 "fit": {"data": "$inputs.history", "value": "y", "time": "t", "adjust": ["growth"]}}},
                     rows, clock=WEEKS, rounds=208)
    assert _param(result, "growth", "rate") == pytest.approx(0.004, abs=0.0004)
    fitted = _param(result, "season", "profile")
    assert max(abs(a - b) for a, b in zip(fitted, profile)) < 0.03


def test_constant_elasticity_is_recovered_by_log_log_regression():
    rng = random.Random(3)
    rows = []
    for _ in range(400):
        price = rng.uniform(10, 30)
        rows.append({"price": price, "units": 50 * (price / 20) ** -1.4 * math.exp(rng.gauss(0, 0.05))})
    result = _fitted({"p": {"kind": "elasticity", "elasticity": -1, "reference": 20,
                            "fit": {"data": "$inputs.history", "value": "units", "x": "price"}}}, rows)
    assert _param(result, "p", "elasticity") == pytest.approx(-1.4, abs=0.03)
    assert result.fits[0].assumed == ["reference"]


def test_count_dispersion_is_recovered_by_the_method_of_moments():
    rng = random.Random(4)
    rows = [{"mean": mu, "y": _negbin(rng, mu, 3)} for mu in (rng.uniform(2, 20) for _ in range(4000))]
    result = _fitted({"c": {"kind": "counts", "fit": {"data": "$inputs.history", "value": "y", "mean": "mean"}}}, rows)
    assert _param(result, "c", "dispersion") == pytest.approx(3, rel=0.15)


def test_a_negative_binomial_refit_started_from_an_earlier_fit_converges_to_the_same_estimates():
    from fg_env.sdk.patterns.numeric import count_regression

    rng = random.Random(9)
    rows, ys, groups, censored = [], [], [], []
    for _ in range(3000):
        group, x = rng.randrange(20), rng.uniform(-1, 1)
        mean = math.exp(1 + 0.05 * group + 0.6 * x)
        demand = _negbin(rng, mean, 4)
        cap = rng.randrange(2, 30)
        rows.append([x])
        groups.append(group)
        ys.append(min(demand, cap))
        censored.append(demand > cap)
    poisson = count_regression(rows, ys, censored=censored, groups=groups, errors=False)
    assert poisson.se == [] and poisson.intercept_se == []
    cold = count_regression(rows, ys, censored=censored, groups=groups, k=4.0)
    warm = count_regression(rows, ys, censored=censored, groups=groups, k=4.0, start=poisson)
    assert warm.coef[0] == pytest.approx(cold.coef[0], rel=1e-5) and warm.coef[0] == pytest.approx(0.6, abs=0.05)
    assert warm.intercepts == pytest.approx(cold.intercepts, rel=1e-5, abs=1e-6)
    assert warm.se == pytest.approx(cold.se, rel=1e-4)


def test_mean_reversion_and_autoregression_are_recovered_from_simulated_paths():
    rng = random.Random(5)
    x, ou = 10.0, []
    decay = math.exp(-0.2)
    for t in range(3000):
        ou.append({"t": t, "y": x})
        x = 10 + (x - 10) * decay + math.sqrt((1 - decay ** 2) / 0.4) * rng.gauss(0, 1)
    result = _fitted({"m": {"kind": "mean_reversion", "mean": 0, "rate": 1,
                            "fit": {"data": "$inputs.history", "value": "y", "time": "t"}}}, ou)
    assert _param(result, "m", "rate") == pytest.approx(0.2, abs=0.03)
    assert _param(result, "m", "mean") == pytest.approx(10, abs=0.3)
    assert _param(result, "m", "sd") == pytest.approx(1, abs=0.1)
    values, rows = [5.0, 5.0], []
    for t in range(3000):
        values.append(5 + 0.5 * (values[-1] - 5) + 0.2 * (values[-2] - 5) + rng.gauss(0, 1))
        rows.append({"t": t, "y": values[-1]})
    result = _fitted({"a": {"kind": "autoregressive", "coefficients": [0, 0],
                            "fit": {"data": "$inputs.history", "value": "y", "time": "t"}}}, rows)
    assert _param(result, "a", "coefficients") == pytest.approx([0.5, 0.2], abs=0.06)


def test_weather_recovers_its_seasonal_normal_and_the_persistence_of_departures():
    contract = world({"t": {"kind": "weather", "mean": 18, "amplitude": 9, "peak": 0.55, "persistence": 0.7, "sd": 3}},
                     clock={"unit": "week", "start": "2000-01-03"}, rounds=1040, metrics={"t": "$pattern.t"})
    temps = fg_env.run(contract, "idle", seed=6).series["t"]
    rows = [{"t": week, "temp": value} for week, value in enumerate(temps)]
    result = _fitted({"w": {"kind": "weather", "mean": 0, "fit": {"data": "$inputs.history", "value": "temp", "time": "t"}}},
                     rows, clock={"unit": "week", "start": "2000-01-03"}, rounds=1040)
    assert _param(result, "w", "mean") == pytest.approx(18, abs=0.5)
    assert _param(result, "w", "amplitude") == pytest.approx(9, abs=0.6)
    assert _param(result, "w", "peak") == pytest.approx(0.55, abs=0.02)
    assert _param(result, "w", "persistence") == pytest.approx(0.7, abs=0.06)


def test_carryover_retain_is_found_by_grid_search():
    rng = random.Random(7)
    stock, rows = 0.0, []
    for t in range(600):
        spend = 10.0 if rng.random() < 0.3 else 0.0
        stock = spend + 0.6 * stock
        rows.append({"t": t, "spend": spend, "visits": 5 + 2 * stock + rng.gauss(0, 1)})
    result = _fitted({"ads": {"kind": "carryover", "input": "$world.spend",
                              "fit": {"data": "$inputs.history", "value": "visits", "time": "t", "x": "spend"}}},
                     rows, world={"spend": 0.0})
    assert _param(result, "ads", "retain") == pytest.approx(0.6, abs=0.03)


def test_saturation_and_bass_diffusion_are_recovered_by_search():
    rng = random.Random(8)
    rows = [{"x": x, "y": 1 + 3 * x * x / (4 + x * x) + rng.gauss(0, 0.05)} for x in (rng.uniform(0, 10) for _ in range(300))]
    result = _fitted({"s": {"kind": "saturation", "shape": 1, "fit": {"data": "$inputs.history", "value": "y", "x": "x"}}}, rows)
    assert _param(result, "s", "limit") == pytest.approx(3, abs=0.2)
    assert _param(result, "s", "half") == pytest.approx(2, abs=0.2)
    assert _param(result, "s", "shape") == pytest.approx(2, abs=0.3)

    def share(tau, p=0.03, q=0.4):
        decay = math.exp(-(p + q) * tau)
        return (1 - decay) / (1 + (q / p) * decay) if tau > 0 else 0.0

    rows = [{"t": t, "new": 1000 * (share(t + 1) - share(t)) + rng.gauss(0, 2)} for t in range(40)]
    result = _fitted({"b": {"kind": "diffusion", "p": 0.1, "q": 0.1, "output": "new",
                            "fit": {"data": "$inputs.history", "value": "new", "time": "t"}}}, rows)
    assert _param(result, "b", "p") == pytest.approx(0.03, abs=0.01)
    assert _param(result, "b", "q") == pytest.approx(0.4, abs=0.05)
    assert _param(result, "b", "market") == pytest.approx(1000, rel=0.08)


def test_a_weibull_hazard_is_recovered_by_maximum_likelihood():
    rng = random.Random(9)
    rows = []
    for _ in range(800):
        life = rng.weibullvariate(10, 1.5)
        age = 0
        while True:
            left = life <= age + 1
            rows.append({"age": age, "left": 1 if left else 0})
            if left:
                break
            age += 1
    result = _fitted({"h": {"kind": "hazard", "form": "weibull",
                            "fit": {"data": "$inputs.history", "value": "left", "x": "age"}}}, rows)
    assert _param(result, "h", "shape") == pytest.approx(1.5, abs=0.15)
    assert _param(result, "h", "scale") == pytest.approx(10, abs=0.8)


SHOP_CATS = [{"category": "pads", "elasticity": -1.3}, {"category": "wipers", "elasticity": -0.6}]
SHOP_SKUS = [{"sku": "p1", "category": "pads", "base": 8.0}, {"sku": "p2", "category": "pads", "base": 20.0},
             {"sku": "w1", "category": "wipers", "base": 12.0}, {"sku": "w2", "category": "wipers", "base": 30.0}]


def _shop(stock_cap=None):
    """A store generating its own weekly history: base × growth × price response × promotion, negative-binomial sales,
    optionally capped by a stock level (stockouts)."""
    sold = "$min($it.demand, $it.stock)" if stock_cap else "$it.demand"
    return {
        "name": "Store history",
        "clock": {"rounds": 156, "unit": "week", "start": "2022-01-03"},
        "inputs": {"growth": {"type": "number", "default": 0.004}, "lift": {"type": "number", "default": 1.5},
                   "k": {"type": "number", "default": 6.0}, "cap": {"type": "number", "default": stock_cap or 0},
                   "cats": {"type": "table", "default": SHOP_CATS}, "skus": {"type": "table", "default": SHOP_SKUS}},
        "types": {"clerk": {"agent": True},
                  "sku": {"props": {"category": "pads", "price": 20.0, "promo": 0.0, "demand": 0, "stock": 0}}},
        "entities": {"clerk": {"type": "clerk"}},
        "population": [{"type": "sku", "from": "$inputs.skus", "id": "{$row.sku}", "props": {"category": "$row.category"}}],
        "records": {"history": {"fields": {"date": "text", "sku": "text", "price": "number", "promo": "number",
                                           "units": "int", "stockout": "int", "demand": "int"}, "notify": False}},
        "patterns": {
            "growth": {"kind": "trend", "form": "exponential", "rate": "$inputs.growth"},
            "demand": {"kind": "product", "table": "$inputs.skus", "column": "sku", "scale": "$row.base", "of": ["growth"]},
            "price_effect": {"kind": "elasticity", "table": "$inputs.cats", "column": "category",
                             "elasticity": "$row.elasticity", "reference": 20},
            "promo": {"kind": "promotion", "keys": "sku", "input": "$it.promo", "lift": "$inputs.lift", "form": "exponential"},
            "sales": {"kind": "counts", "dispersion": "$inputs.k"},
            "wobble": {"kind": "noise", "sd": 0.2}, "roll": {"kind": "noise", "dist": "uniform"},
        },
        "actions": {"wait": {"by": "clerk", "do": []}},
        "events": [
            {"phase": "start", "each": "sku", "do": [
                "$it.price = $round(20 * $exp($pattern.wobble($it)), 2)",
                "$it.promo = 0.3 if $pattern.roll($it) < 0.15 else 0",
                "$it.stock = $floor($inputs.cap * $row_base($it) * (0.5 + $pattern.roll($it.id + 'stock')))"]},
            {"phase": "end", "each": "sku", "do": [
                "$it.demand = $pattern.sales($pattern.demand($it) * $pattern.price_effect($it.price, $it.category) * $pattern.promo($it), $it)",
                {"post": "history", "date": "$clock.date", "sku": "$it.id", "price": "$it.price", "promo": "$it.promo",
                 "units": sold, "stockout": f"1 if {sold} < $it.demand else 0", "demand": "$it.demand"}]},
        ],
        "defs": {"row_base": {"args": ["item"], "expr": "$pattern.demand($item)"}},
    }


def _history(contract, seed=11):
    env = fg_env.load(contract, seed=seed)
    env.run("idle")
    return [dict(row) for row in env.world.records_store["history"]]


def _shop_fit(contract, rows, **fit):
    guess = copy.deepcopy(contract)
    guess["inputs"]["history"] = {"type": "table", "default": rows}
    guess["inputs"]["cats"]["default"] = [{**c, "elasticity": -1.0} for c in SHOP_CATS]
    guess["inputs"]["skus"]["default"] = [{**s, "base": 10.0} for s in SHOP_SKUS]
    guess["inputs"]["growth"]["default"], guess["inputs"]["lift"]["default"] = 0.0, 0.1
    guess["patterns"]["demand"]["fit"] = {"data": "$inputs.history", "value": "units", "time": "date", "key": "sku",
                                          "x": {"price_effect": {"column": "price", "key": "$row.category"}, "promo": "promo"},
                                          "noise": "sales", **fit}
    return fg_env.fit_patterns(guess)


def test_a_joint_product_fit_separates_price_from_promotion_and_recovers_every_parameter():
    lifts = []
    for seed in (20, 21, 22):
        result = _shop_fit(_shop(), _history(_shop(), seed=seed))
        inputs = result.contract["inputs"]
        lift, lift_se = inputs["promo_lift"]["default"], inputs["promo_lift_se"]["default"]
        assert abs(lift - 1.5) < 3 * lift_se
        lifts.append(lift)
        assert abs(inputs["growth_rate"]["default"] - 0.004) < 3 * inputs["growth_rate_se"]["default"]
        assert inputs["sales_dispersion"]["default"] == pytest.approx(6, rel=0.35)
        for row in inputs["price_effect_fit"]["default"]:
            true = next(c["elasticity"] for c in SHOP_CATS if c["category"] == row["category"])
            assert abs(row["elasticity"] - true) < 3 * row["elasticity_se"]
        for row in inputs["demand_fit"]["default"]:
            true = next(s["base"] for s in SHOP_SKUS if s["sku"] == row["sku"])
            assert row["scale"] == pytest.approx(true, rel=0.2)
        assert "joint count regression" in result.fits[0].method
    assert statistics.fmean(lifts) == pytest.approx(1.5, abs=0.2)


def test_stockout_rows_fitted_as_censored_recover_demand_better_than_dropping_them():
    contract = _shop(stock_cap=1.1)
    rows = _history(contract, seed=12)
    assert 0.1 < statistics.fmean(r["stockout"] for r in rows) < 0.5

    def base_error(result):
        return statistics.fmean(abs(row["scale"] / next(s["base"] for s in SHOP_SKUS if s["sku"] == row["sku"]) - 1)
                                for row in result.contract["inputs"]["demand_fit"]["default"])

    censored = base_error(_shop_fit(contract, rows, censored="stockout"))
    dropped = base_error(_shop_fit(contract, rows, where="$row.stockout == 0"))
    naive = base_error(_shop_fit(contract, rows))
    assert censored < 0.12 and censored < dropped and censored < naive


def test_fitted_parameters_are_written_back_as_inputs_with_errors_that_runs_draw_from():
    rng = random.Random(13)
    rows = [{"price": p, "units": 50 * (p / 20) ** -1.2 * math.exp(rng.gauss(0, 0.3))} for p in (rng.uniform(10, 30) for _ in range(60))]
    result = _fitted({"p": {"kind": "elasticity", "elasticity": -1, "reference": 20,
                            "fit": {"data": "$inputs.history", "value": "units", "x": "price"}}}, rows,
                     metrics={"e": "$log($pattern.p(40)) / $log(2)"})
    fitted = result.contract
    assert fitted["patterns"]["p"]["elasticity"] == "$inputs.p_elasticity"
    assert fitted["patterns"]["p"]["uncertainty"] == {"elasticity": "$inputs.p_elasticity_se * $inputs.parameter_uncertainty"}
    estimate, error = fitted["inputs"]["p_elasticity"]["default"], fitted["inputs"]["p_elasticity_se"]["default"]
    draws = [fg_env.run(fitted, "idle", seed=s, rounds=1).series["e"][0] for s in range(60)]
    assert statistics.pstdev(draws) == pytest.approx(error, rel=0.3)
    exact = fg_env.run(fitted, "idle", seed=1, rounds=1, inputs={"parameter_uncertainty": 0}).series["e"][0]
    assert exact == pytest.approx(estimate)
    assert result.priors == {"p_elasticity": {"dist": "normal", "mean": estimate, "sd": error}}


def test_fit_reads_data_files_from_the_data_directory(tmp_path):
    rng = random.Random(14)
    with open(tmp_path / "history.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t", "y"])
        for t in range(100):
            writer.writerow([t, 2 + 0.5 * t + rng.gauss(0, 0.5)])
    contract = world({"g": {"kind": "trend", "fit": {"data": "$inputs.history", "value": "y", "time": "t"}}},
                     inputs={"history": {"type": "table", "source": "history.csv"}})
    result = fg_env.fit_patterns(contract, data_dir=tmp_path)
    assert _param(result, "g", "slope") == pytest.approx(0.5, abs=0.02)
    assert result.contract["inputs"]["history"] == {"type": "table", "source": "history.csv"}


def test_decompose_shows_what_each_factor_adds_to_a_product():
    contract = world({"trend": {"kind": "trend", "start": 1, "slope": 0.5},
                      "season": {"kind": "seasonal", "period": 2, "profile": [1, 2]},
                      "demand": {"kind": "product", "scale": 10, "of": ["trend", "season"]}}, rounds=3)
    parts = fg_env.decompose(contract, "demand")
    assert [row["total"] for row in parts.rows] == pytest.approx([10, 30, 20])
    second = parts.rows[1]
    assert second["factors"] == {"trend": 1.5, "season": 2}
    assert second["adds"]["season"] == pytest.approx(15) and second["adds"]["trend"] == pytest.approx(10)
    assert "season (adds)" in parts.table()


def test_describe_lists_every_pattern_in_plain_words():
    contract = world({"price_effect": {"kind": "elasticity", "elasticity": -1.4, "reference": 20,
                                       "description": "Shoppers buy less when prices rise"}})
    text = fg_env.describe(contract).markdown
    assert "### World patterns" in text and "Shoppers buy less when prices rise" in text
    assert "`$pattern.price_effect(price)`" in text
