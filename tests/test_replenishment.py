"""The replenishment mode: (s, S), (s, Q), order-up-to, service-level and agents' orders keep a demand mechanism's items
in stock, within case packs, capacity, a budget and the account, with lead times drawn per order that can be fitted
back from the orders it records."""
import copy
import json
import math
import statistics
from statistics import NormalDist

import pytest

import fg_env

from store_fixtures import item, store, with_ledger


def reorder(**config):
    return {"kind": "economy", "mode": "replenishment", "demand": "shop", **config}


def placed(env, sku):
    return [entry for entry in item(env, sku)["reorder_pipeline"]]


def _orders_by_round(contract, rounds, seed=1):
    """Every order as (item, round placed, qty, position before it) over ``rounds`` rounds."""
    env = fg_env.load(contract, seed=seed)
    orders = []
    for _ in range(rounds):
        env.run("idle", rounds=1)
        for sku in ("a", "b"):
            p = item(env, sku)
            if p["reorder_pipeline"] and p["reorder_pipeline"][-1][2] == env.world.round and \
                    (not orders or (sku, env.world.round) not in [(o[0], o[1]) for o in orders]):
                qty = p["reorder_pipeline"][-1][1]
                orders.append((sku, env.world.round, qty, p["stock"] + p["reorder_on_order"] - p["shop_backlog"] - qty))
    return env, orders


def test_s_S_orders_up_to_S_in_case_packs_once_the_position_falls_to_s_and_orders_arrive_after_their_lead_time():
    contract = store(rounds=30)
    contract["mechanisms"]["reorder"] = reorder(policy="s_S", reorder_point=20, order_up_to=45, case_pack=4, lead_time=2)
    env, orders = _orders_by_round(contract, 30)
    assert len(orders) >= 4
    for _, _, qty, position in orders:
        assert position <= 20 and qty % 4 == 0 and position + qty >= 45 > position + qty - 4
    for sku in ("a", "b"):
        assert all(entry[0] == entry[2] + 2 for entry in item(env, sku)["reorder_pipeline"])
    arrived = sum(item(env, sku)["reorder_units_ordered"] - item(env, sku)["reorder_on_order"] for sku in ("a", "b"))
    assert env.world.props["shop_stock_flows"]["reorder"] == arrived


def test_s_Q_orders_whole_multiples_of_Q_until_the_position_is_above_s():
    contract = store(rounds=30)
    contract["mechanisms"]["reorder"] = reorder(policy="s_Q", reorder_point=25, order_qty=10, lead_time=1)
    _, orders = _orders_by_round(contract, 30)
    assert orders
    for _, _, qty, position in orders:
        assert qty % 10 == 0 and position <= 25 < position + qty and position + qty - 10 <= 25


def test_the_service_policy_orders_up_to_lead_time_demand_plus_safety_stock_from_forecast_and_lead_time_spread():
    contract = store(noise="sales")
    contract["patterns"]["lead_noise"] = {"kind": "noise", "dist": "lognormal", "mean": 0, "sd": 0.25}
    contract["mechanisms"]["reorder"] = reorder(policy="service", service_level=0.9, review_every=2,
                                                lead_time={"pattern": "lead_noise", "scale": 2})
    env = fg_env.load(contract, seed=2)
    env.run("idle", rounds=1)
    for sku in ("a", "b"):
        p = item(env, sku)
        forecast, variance = p["shop_expected"], p["shop_variance"]
        lead = 2 * math.exp(0.25 ** 2 / 2)
        lead_sd = lead * math.sqrt(math.expm1(0.25 ** 2))
        cover = lead + 2
        target = math.ceil(forecast * cover + NormalDist().inv_cdf(0.9) * math.sqrt(cover * variance + forecast ** 2 * lead_sd ** 2))
        assert p["reorder_target"] == target
        position = p["stock"] - p["shop_backlog"]
        assert p["reorder_on_order"] == max(0, target - position)


def test_a_higher_service_level_serves_more_demand_and_holds_more_stock():
    contract = store(rounds=40, noise="sales")
    contract["inputs"]["level"] = {"type": "number", "default": 0.5}
    contract["mechanisms"]["reorder"] = reorder(policy="service", service_level="$inputs.level", lead_time=2,
                                                holding_cost="$it.list * 0.01")
    contract["arms"] = {"low": {"inputs": {"level": 0.2}}, "high": {"inputs": {"level": 0.98}}}
    exp = fg_env.experiment(contract, arms=["low", "high"], runs=3, seed=3)
    low, high = ([run.outputs for run in exp.arms[arm].runs] for arm in ("low", "high"))
    assert statistics.fmean(o["shop_fill_rate"] for o in high) > statistics.fmean(o["shop_fill_rate"] for o in low) + 0.03
    assert statistics.fmean(o["reorder_average_stock_value"] for o in high) > statistics.fmean(o["reorder_average_stock_value"] for o in low)
    assert [o["shop_demand"] for o in high] == [o["shop_demand"] for o in low]  # the same customers in both arms


def test_a_budget_goes_to_the_most_urgent_item_first_and_the_account_pays_for_orders():
    contract = with_ledger(store(account="store", currency="cash"))
    contract["mechanisms"]["reorder"] = reorder(policy="order_up_to", order_up_to=60, budget=50, lead_time=1)
    contract["invariants"] = ["$conserved('money')"]
    env = fg_env.load(contract, seed=4)
    env.run("idle", rounds=1)
    a, b = item(env, "a"), item(env, "b")
    assert a["reorder_last_order"] == 10 and b["reorder_orders"] == 0  # a covers fewer weeks: 10 units at 5 spend all 50
    assert env.world.props["money_flows"]["reorder_suppliers"]["cash"] == -50
    result = env.run("idle")
    assert result.status != "failed", result.error
    assert result.outputs["reorder_purchases"] == pytest.approx(-env.world.props["money_flows"]["reorder_suppliers"]["cash"])


def test_capacity_caps_orders_and_agents_orders_that_do_not_fit_are_refused_with_the_reason():
    contract = store(rounds=2)
    contract["entities"]["ann"] = {"type": "buyer"}
    contract["mechanisms"]["reorder"] = reorder(policy="manual", who="buyer", case_pack=6, capacity="32 if $it.id == 'a' else 100")
    results = []

    def ann(wake):
        if wake.round == 1:
            results.append(wake.call("reorder_order", {"item": "a", "qty": 5}))
            results.append(wake.call("reorder_order", {"item": "b", "qty": 1}))
        wake.end()

    env = fg_env.load(contract, seed=5)
    env.run({"buyer": ann}, rounds=1)
    refused, accepted = results
    assert not refused.ok and "No room" in refused.text
    assert accepted.ok and item(env, "b")["reorder_on_order"] == 6 and item(env, "a")["reorder_orders"] == 0


def test_lead_times_drawn_per_order_are_recorded_and_fitted_back_from_the_orders():
    skus = [{"sku": f"s{i}", "part": "pads", "base": 20.0, "list": 10.0, "sibling": "", "stock": 0} for i in range(6)]
    contract = store(skus=skus, rounds=120)
    contract["patterns"]["lead_noise"] = {"kind": "noise", "dist": "lognormal", "mean": 0, "sd": 0.3}
    contract["mechanisms"]["reorder"] = reorder(policy="order_up_to", order_up_to=40, record=True,
                                                lead_time={"pattern": "lead_noise", "scale": 3})
    env = fg_env.load(contract, seed=6)
    env.run("idle")
    rows = [dict(row) for row in env.world.records_store["reorder_orders"]]
    assert len(rows) > 300 and all(row["arrived"] - row["placed"] == max(1, round(row["lead_time"])) for row in rows)
    assert all(row["lead_time"] == pytest.approx(3 * row["factor"], abs=1e-5) for row in rows)
    fit = copy.deepcopy(contract)
    fit["inputs"]["orders"] = {"type": "table", "default": rows}
    fit["patterns"]["lead_noise"] = {"kind": "noise", "dist": "lognormal", "fit": {"data": "$inputs.orders", "value": "factor"}}
    fitted = fg_env.analysis.fit_patterns(fit).contract["inputs"]
    assert fitted["lead_noise_sd"]["default"] == pytest.approx(0.3, abs=0.03)
    assert fitted["lead_noise_mean"]["default"] == pytest.approx(0.0, abs=0.03)


def test_backorders_accrue_their_cost_and_lower_the_inventory_position():
    skus = [{"sku": "a", "part": "pads", "base": 8.0, "list": 10.0, "sibling": "", "stock": 0}]
    contract = store(skus=skus, backorder=1, rounds=3)
    contract["mechanisms"]["reorder"] = reorder(policy="s_S", reorder_point=0, order_up_to="$position * 0 + 5",
                                                backorder_cost=2, stockout_cost=100, lead_time=10)
    env = fg_env.load(contract, seed=7)
    env.run("idle", rounds=1)
    a = item(env, "a")
    waiting = a["shop_backlog"]
    assert waiting > 0 and a["reorder_last_order"] == 5 + waiting  # up to 5 above zero, counting what customers wait for
    env.run("idle", rounds=1)
    a = item(env, "a")
    assert a["reorder_backorder_cost"] == 2 * (waiting + a["shop_backlog"]) and a["reorder_stockout_cost"] == 0


@pytest.mark.parametrize("mechanisms, path, words", [
    ({"reorder": reorder(policy="s_S", reorder_point=5), "shop": None}, "mechanisms.reorder.demand", "declared after"),
    ({"reorder": reorder(policy="s_S", reorder_point=5)}, "mechanisms.reorder.order_up_to", "needs `order_up_to`"),
    ({"reorder": reorder(policy="just_in_time")}, "mechanisms.reorder.policy", "not a policy"),
    ({"reorder": reorder(lead_time={"pattern": "sales"}, policy="service")}, "mechanisms.reorder.lead_time.pattern", "not a noise pattern"),
    ({"reorder": reorder(demand="money", policy="service")}, "mechanisms.reorder.demand", "not a declared economy (demand)"),
])
def test_config_mistakes_are_reported_at_their_field_with_what_to_do(mechanisms, path, words):
    contract = with_ledger(store())
    for name, config in mechanisms.items():
        if config is None:  # move the demand after the replenishment
            contract["mechanisms"][name] = contract["mechanisms"].pop(name)
        else:
            contract["mechanisms"][name] = config
    issues = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert any(i.path == path and (words in i.message or words in (i.fix or "")) for i in issues), issues


def test_a_demand_without_stock_cannot_be_replenished():
    contract = store()
    del contract["mechanisms"]["shop"]["stock"]
    contract["mechanisms"]["reorder"] = reorder(policy="service")
    issues = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert any(i.path == "mechanisms.reorder.demand" and "keeps no stock" in i.message for i in issues), issues


def test_a_replenished_store_resumes_exactly_from_a_json_snapshot_a_clone_and_a_fork():
    contract = with_ledger(store(rounds=16, account="store", currency="cash", backorder=0.5, noise="sales"))
    contract["mechanisms"]["money"]["currencies"]["cash"]["credit"] = 10 ** 6
    contract["patterns"]["lead_noise"] = {"kind": "noise", "dist": "lognormal", "mean": 0, "sd": 0.3}
    contract["inputs"]["level"] = {"type": "number", "default": 0.9}
    contract["mechanisms"]["reorder"] = reorder(policy="service", service_level="$inputs.level", case_pack=3,
                                                lead_time={"pattern": "lead_noise", "scale": 2}, holding_cost=0.1)
    straight = fg_env.load(contract, seed=8).run("idle").to_dict()
    env = fg_env.load(contract, seed=8)
    env.run("idle", rounds=6)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run("idle").to_dict() == straight
    assert env.clone().run("idle").to_dict() == straight
    assert env.fork(inputs={"level": 0.99}).run("idle").outputs["reorder_average_stock_value"] > \
        straight["outputs"]["reorder_average_stock_value"]


def test_the_guide_documents_demand_and_replenishment_with_their_actions_and_functions():
    demand, replenishment = fg_env.guide("economy.demand"), fg_env.guide("economy.replenishment")
    assert "### `economy.demand`" in demand and "- `receive`" in demand and "- `remove`" in demand
    assert "- `trade`" not in demand and "- `open`" not in demand
    for field in ("rate", "factors", "noise", "stock", "substitutes", "spill", "backorder", "segments", "returns", "record"):
        assert f"- `{field}`" in demand, field
    assert "### `economy.replenishment`" in replenishment and "- `order`" in replenishment and "- `review`" not in replenishment
    for field in ("policy", "reorder_point", "order_up_to", "order_qty", "service_level", "lead_time", "case_pack", "budget",
                  "capacity", "holding_cost", "stockout_cost", "backorder_cost"):
        assert f"- `{field}`" in replenishment, field
    everything = fg_env.guide("all")
    for fn in ("$demand_totals(", "$replenishment_totals(", "$stock_conserved("):
        assert fn in everything
    assert "supply_chain, demand, replenishment |" in fg_env.guide()
