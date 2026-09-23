"""The demand mode: demand drawn from patterns and served from stock — true demand and lost sales kept apart, substitutes,
backorders, segments with their own prices and returns, revenue into a ledger — conserved, resumable, and recorded in
the columns fit_patterns reads."""
import copy
import json

import pytest

import fg_env

from store_fixtures import item, store, with_ledger


def test_sales_are_capped_by_stock_and_lost_sales_are_kept_apart_from_true_demand():
    env = fg_env.load(store(), seed=1)
    ran_out = False
    for _ in range(20):
        env.run("idle", rounds=1)
        for sku in ("a", "b"):
            p = item(env, sku)
            assert p["shop_sold"] + p["shop_lost"] == p["shop_demand"]
            assert p["shop_stockout"] == (p["shop_lost"] > 0)
            ran_out = ran_out or p["shop_stockout"]
    out = env.result().outputs
    assert ran_out and out["shop_sold"] == 60 and out["shop_demand"] > 60
    assert out["shop_sold"] + out["shop_lost"] == out["shop_demand"]
    assert out["shop_fill_rate"] == pytest.approx(out["shop_sold"] / out["shop_demand"], abs=1e-4)
    assert out["shop_sold_by_item"] == {"a": 30, "b": 30} and out["shop_sold_by_group"] == {"pads": 60}
    assert env.world.props["shop_stock_flows"] == {"sold": -60}


def test_expected_demand_is_the_rate_times_each_factor_read_for_the_item():
    contract = store(rate="base", promotion="0.25 if $round == 2 else 0", price="$it.list * (1 - $it.shop_promo)",
                     factors=[{"pattern": "price_effect", "key": "$it.part", "driver": "$price / $it.list"}, "promo",
                              "substitution", "1.5 if $it.id == 'a' else 1.0"], noise="sales")
    contract["patterns"].update({
        "growth": {"kind": "trend", "form": "exponential", "rate": 0.02},
        "base": {"kind": "product", "table": "$inputs.skus", "column": "sku", "scale": "$row.base", "of": ["growth"]},
        "price_effect": {"kind": "elasticity", "keys": ["pads"], "elasticity": -1.5, "reference": 1.0},
        "promo": {"kind": "promotion", "keys": "sku", "input": "$it.shop_promo", "lift": 0.8},
        "substitution": {"kind": "cross_price", "keys": "sku", "reference": {"a": 10.0, "b": 8.0}, "own": -0.5, "cross": 0.4}})
    env = fg_env.load(contract, seed=2)
    env.run("idle", rounds=2)
    runtime = env.world.patterns
    a = item(env, "a")
    assert a["shop_promo"] == 0.25 and a["shop_price"] == 7.5
    expected = (runtime.call("base", ["a"], "test") * runtime.call("price_effect", [0.75, "pads"], "test")
                * runtime.call("promo", ["a"], "test") * runtime.call("substitution", [{"a": 7.5, "b": 6.0}, "a"], "test") * 1.5)
    assert a["shop_expected"] == pytest.approx(expected)
    assert a["shop_variance"] == pytest.approx(expected + expected ** 2 / 4)


def test_unmet_demand_buys_a_substitute_when_it_can():
    skus = [{"sku": "a", "part": "pads", "base": 8.0, "list": 10.0, "sibling": "b", "stock": 0},
            {"sku": "b", "part": "pads", "base": 4.0, "list": 8.0, "sibling": "a", "stock": 1000}]
    env = fg_env.load(store(skus=skus, substitutes="[$it.sibling]", spill=1), seed=3)
    env.run("idle", rounds=5)
    a, b = item(env, "a"), item(env, "b")
    assert a["shop_sold_total"] == 0 and a["shop_lost_total"] == 0
    assert a["shop_substituted_total"] == a["shop_demand_total"] > 0
    assert b["shop_spill_in_total"] == a["shop_demand_total"]
    assert b["shop_sold_total"] == b["shop_served_total"] + b["shop_spill_in_total"]
    assert env.result().outputs["shop_lost"] == 0


def test_unmet_demand_that_waits_is_served_first_when_stock_arrives():
    skus = [{"sku": "a", "part": "pads", "base": 8.0, "list": 10.0, "sibling": "b", "stock": 0}]
    contract = store(skus=skus, backorder=1)
    contract["events"] = [{"at": 4, "phase": "start", "each": "sku",
                           "do": [{"economy": "shop", "action": "receive", "item": "$it", "qty": 50, "source": "supplier"}]}]
    env = fg_env.load(contract, seed=4)
    env.run("idle", rounds=3)
    a = item(env, "a")
    waiting = a["shop_backlog"]
    assert waiting == a["shop_demand_total"] > 0 and a["shop_lost_total"] == 0
    env.run("idle", rounds=1)
    a = item(env, "a")
    assert a["shop_sold"] >= min(waiting, 50)
    assert a["shop_backlog"] == waiting + a["shop_demand"] - a["shop_sold"]
    assert env.world.props["shop_stock_flows"] == {"supplier": 50, "sold": -a["shop_sold"]}


def test_segments_buy_at_their_own_prices_send_units_back_and_money_is_conserved():
    skus = [{"sku": "a", "part": "pads", "base": 8.0, "list": 10.0, "sibling": "b", "stock": 1000},
            {"sku": "b", "part": "pads", "base": 4.0, "list": 8.0, "sibling": "a", "stock": 1000}]
    contract = with_ledger(store(skus=skus, rounds=12, returns={"rate": 0.5, "delay": 2, "restock": 0.5}, account="store",
                                 currency="cash", segments={"bulk": {"rate": 3, "price": "$price * 0.8", "where": "$it.id == 'a'"}}))
    contract["invariants"] = ["$conserved('money')"]
    env = fg_env.load(contract, seed=5)
    result = env.run("idle")
    assert result.status != "failed", result.error
    out = result.outputs
    assert out["shop_revenue_by_segment"]["bulk"] == pytest.approx(8.0 * out["shop_sold_by_segment"]["bulk"])
    assert out["shop_returned_by_segment"]["bulk"] == 0 and out["shop_returned"] > 0
    flows = env.world.props["money_flows"]
    refunds = -flows["shop_refunds"]["cash"]
    assert flows["shop_sales"]["cash"] == pytest.approx(out["shop_revenue"])
    assert env.world.entities["store"].properties["cash"] == pytest.approx(100 + out["shop_revenue"] - refunds)
    restocked = env.world.props["shop_stock_flows"]["returned"]
    assert 0 < restocked < out["shop_returned"]
    assert 0 < out["shop_margin"] < out["shop_revenue"] - refunds


def test_stock_changed_outside_the_mechanism_breaks_its_invariant_with_the_fix():
    contract = store()
    contract["events"] = [{"phase": "start", "each": "sku", "do": ["$it.stock += 1"]}]
    result = fg_env.load(contract, seed=1).run("idle")
    assert result.status == "failed"
    assert "receive and remove actions" in str(result.error)


def test_the_history_record_holds_what_fit_patterns_reads_and_the_elasticity_is_refitted():
    skus = [{"sku": f"s{i}", "part": "pads", "base": 6.0 + 3 * i, "list": 10.0, "sibling": "", "stock": 10 ** 6}
            for i in range(4)]
    truth = store(skus=skus, rounds=104, record=True, price="$round($it.list * $pattern.wobble($it), 2)", rate="demand",
                  factors=[{"pattern": "price_effect", "driver": "$price / $it.list"}], noise="sales")
    truth["patterns"].update({
        "wobble": {"kind": "noise", "dist": "lognormal", "mean": 0, "sd": 0.2},
        "growth": {"kind": "trend", "form": "exponential", "rate": 0.0, "origin": "2026-01-05"},
        "demand": {"kind": "product", "table": "$inputs.skus", "column": "sku", "scale": "$row.base", "of": ["growth"]}})
    env = fg_env.load(truth, seed=6)
    env.run("idle")
    rows = [dict(row) for row in env.world.records_store["shop_history"]]
    assert set(rows[0]) >= {"time", "item", "segment", "units", "stockout", "demand", "lost", "stock", "price", "promo",
                            "price_effect"}
    guess = copy.deepcopy(truth)
    guess["inputs"]["history"] = {"type": "table", "default": rows}
    guess["patterns"]["price_effect"]["elasticity"] = -0.5
    guess["patterns"]["demand"]["fit"] = {"data": "$inputs.history", "value": "units", "time": "time", "key": "item",
                                          "censored": "stockout", "noise": "sales", "x": {"price_effect": "price_effect"}}
    fitted = fg_env.analysis.fit_patterns(guess).contract["inputs"]
    assert abs(fitted["price_effect_elasticity"]["default"] + 1.3) < 3 * fitted["price_effect_elasticity_se"]["default"]


@pytest.mark.parametrize("change, path, words", [
    ({"factors": ["nope"]}, "mechanisms.shop.factors[0]", "not a declared pattern"),
    ({"factors": ["sales"]}, "mechanisms.shop.factors[0]", "name a counts pattern as `noise`"),
    ({"noise": "price_effect"}, "mechanisms.shop.noise", "not a counts pattern"),
    ({"account": "store"}, "mechanisms.shop.account", "go together"),
    ({"items": "widget"}, "mechanisms.shop.items", "not a declared type"),
    ({"segments": {"retail": {"rate": 2}}}, "mechanisms.shop.segments", "main segment"),
])
def test_config_mistakes_are_reported_at_their_field_with_what_to_do(change, path, words):
    issues = [i for i in fg_env.check(store(**change), rounds=0) if i.severity == "error"]
    assert any(i.path == path and (words in i.message or words in (i.fix or "")) for i in issues), issues


def test_a_store_run_resumes_exactly_from_a_json_snapshot_and_a_clone():
    contract = with_ledger(store(substitutes="[$it.sibling]", spill=0.5, backorder=0.3, account="store", currency="cash",
                                 returns={"rate": 0.2, "delay": 3}, segments={"bulk": {"rate": 2, "price": "$price * 0.9"}},
                                 noise="sales"))
    contract["events"] = [{"every": 3, "phase": "start", "each": "sku",
                           "do": [{"economy": "shop", "action": "receive", "item": "$it", "qty": 25, "source": "supplier"}]}]
    straight = fg_env.load(contract, seed=7).run("idle").to_dict()
    env = fg_env.load(contract, seed=7)
    env.run("idle", rounds=8)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run("idle").to_dict() == straight
    assert env.clone().run("idle").to_dict() == straight
