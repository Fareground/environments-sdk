"""Every mechanism example the reference pages show works as written: placed in a small contract of the parts it
assumes, it checks with no error and plays three rounds.

The reference shows each example under the name the page gives it (``my_<mode>``, or the one name a mode must have).
An example that needs more than a type for its `who` names what else it assumes below: a new mode whose example
does not work fails here until its example works, or its host says what it assumes.
"""
import copy

import pytest

import fg_env
import fg_env.mechanisms  # noqa: F401  (registers them)
from fg_env.host.stubs import StubEvaluator, StubGameMaster
from fg_env.registry import FAMILIES

_CALENDAR = {"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}}
_SKUS = {"types": {"sku": {"props": {"price": 10.0, "promo": 0.0, "list_price": 10.0, "shop_promo": 0.0,
                                     "unit_cost": 4.0, "category": "tools", "sibling": "sku_1", "shop_rate": 2.0,
                                     "lead_weeks": 1, "case_pack": 6, "stock": 20}}},
         "entities": {"sku": {"type": "sku", "count": 2}}}
_CASH = {"economy": {"kind": "economy", "mode": "ledger", "who": ["household"], "currencies": {"cash": {"start": 100}}}}

#: What each example assumes besides a type (of agents) for its `who`, merged into the contract it plays in.
HOSTS = {
    "market.prediction": {"world": {"truth": "yes"}},
    "market.posted": {"types": {"farmer": {"agent": True}}, "entities": {"ana": {"type": "farmer"}}},
    "economy.inventory": {"types": {"villager": {"agent": True, "props": {"hunger": 10}}}},
    "economy.ledger": {"types": {"household": {"agent": True}, "shop": {"agent": True}},
                       "entities": {"household": {"type": "household", "count": 2}, "shop": {"type": "shop"}}},
    "economy.production": {
        "types": {"villager": {"agent": True}, "place": {}},
        "entities": {"bakery": {"type": "place"}, "forest": {"type": "place"},
                     "villager": {"type": "villager", "count": 2, "at": "forest"}},
        "space": {"graph": {"nodes": ["bakery", "forest"], "edges": [["bakery", "forest"]]}},
        "mechanisms": {"goods": {"kind": "economy", "mode": "inventory", "who": "villager",
                                 "items": {"flour": {}, "bread": {}, "berries": {}}}}},
    "economy.supply_chain": {
        "types": {"stage_node": {"agent": True}},
        "entities": {name: {"type": "stage_node"} for name in ("retailer", "wholesaler", "distributor", "factory")},
        "mechanisms": {"stock": {"kind": "economy", "mode": "inventory", "who": "stage_node",
                                 "items": {"beer": {}}}}},
    "economy.demand": {**_SKUS, "mechanisms": {
        "demand": {"kind": "pattern", "mode": "cycle", "period": 4, "amplitude": 0.2, "level": 5},
        "sales": {"kind": "pattern", "mode": "counts"},
        "price_effect": {"kind": "pattern", "mode": "elasticity", "elasticity": -1.5, "reference": 1},
        "promo": {"kind": "pattern", "mode": "promotion", "input": "$it.promo", "keys": "sku", "lift": 0.5},
        "promo_week": {"kind": "pattern", "mode": "draw", "dist": "normal", "mean": 0, "sd": 1, "keys": ["tools"]}}},
    "economy.replenishment": {**_SKUS, "mechanisms": {
        "shop": {"kind": "economy", "mode": "demand", "items": "sku", "stock": "stock", "price": "$it.price",
                 "rate": 2},
        "lead_noise": {"kind": "pattern", "mode": "noise", "dist": "lognormal", "sd": 0.1, "keys": "sku"}}},
    "economy.queue": {"inputs": {"calls": {"type": "list", "default": [20, 30, 25]},
                                 "staffing": {"type": "list", "default": [4, 5, 5]}},
                      "clock": {"rounds": 3, "unit": "second"}},
    "agreements.bookings": {"types": {"household": {"agent": True}, "restaurant": {"agent": True}},
                            "entities": {"household": {"type": "household", "count": 2},
                                         "bistro": {"type": "restaurant"}},
                            "mechanisms": {"economy": {**_CASH["economy"], "who": ["household", "restaurant"]}}},
    "agreements.negotiation": {"types": {"country": {"agent": True, "props": {"weight": 1.0}}},
                               "mechanisms": {"economy": {**_CASH["economy"], "who": ["country"],
                                                          "currencies": {"credits": {"start": 1000}}}}},
    "agreements.subscriptions": {"types": {"household": {"agent": True}, "cafe": {"agent": True}},
                                 "entities": {"household": {"type": "household", "count": 2},
                                              "bean_bar": {"type": "cafe"}},
                                 "mechanisms": {"economy": {**_CASH["economy"], "who": ["household", "cafe"]}}},
    "groups.roles": {"entities": {"player": {"type": "player", "count": 5}}},
    "decision.deliberation": {"types": {"moderator": {"agent": True}}, "entities": {"chair": {"type": "moderator"}}},
    "decision.procedure": {"types": {"member": {"agent": True, "props": {"said": 0, "votes": 0}}},
                           "entities": {"member": {"type": "member", "count": 3}},
                           "actions": {"speak": {"by": "member", "do": "$actor.said += 1"},
                                       "vote": {"by": "member", "do": "$actor.votes += 1"}}},
    "game.board": {"types": {"player": {"agent": True, "props": {"side": {"type": "enum", "values": ["x", "o"],
                                                                         "default": "x"}}}},
                   "entities": {"xena": {"type": "player", "props": {"side": "x"}},
                                "otto": {"type": "player", "props": {"side": "o"}}}},
    "game.pot": {"types": {"player": {"agent": True, "props": {"seat": 0}}},
                 "entities": {"ann": {"type": "player", "props": {"seat": 1}},
                              "bo": {"type": "player", "props": {"seat": 2}}},
                 "mechanisms": {"cards": {"kind": "game", "mode": "cards", "who": "player", "deal": "never",
                                          "zones": {"board": "public"}}}},
    "game.status": {"types": {"unit": {"agent": True, "props": {"hp": 20, "armor": 0}}},
                    "actions": {"attack": {"by": "unit", "do": "$actor.hp += 0"}}},
    "groups.matching": {"types": {"school": {"agent": True, "props": {"capacity": 2}}},
                        "entities": {"school": {"type": "school", "count": 2}}},
    "social.diffusion": {"entities": {"u1": {"type": "account"}, "u2": {"type": "account"}},
                         "relations": {"follows": {"links": [{"from": "u2", "to": "u1"}]}}},
    "social.feed": {"types": {"moderator": {"agent": True}}, "entities": {"mod": {"type": "moderator"}}},
    "host.judge": {"types": {"debater": {"agent": True, "props": {"score": 0}}},
                   "records": {"speeches": {"fields": {"text": "text"}}},
                   "actions": {"speak": {"by": "debater", "params": {"text": {"type": "text"}},
                                         "do": [{"post": "speeches", "text": "$params.text"}]}}},
    "host.game_master": {"types": {"adventurer": {"agent": True, "props": {"health": 8, "gold": 10}}, "room": {}},
                         "entities": {"hall": {"type": "room"}, "yard": {"type": "room"},
                                      "adventurer": {"type": "adventurer", "count": 2, "at": "hall"}},
                         "space": {"graph": {"nodes": ["hall", "yard"], "edges": [["hall", "yard"]]}}},
    "host.personas": {"types": {"shopper": {"agent": True, "props": {"age": 30, "budget": 50}}}},
    "host.recap": {"types": {"member": {"agent": True}}, "entities": {"member": {"type": "member", "count": 2}},
                   "records": {"board": {"fields": {"text": "text"}}},
                   "actions": {"post": {"by": "member", "params": {"text": {"type": "text"}},
                                        "do": [{"post": "board", "text": "$params.text"}]}}},
    "host.feed": {"world": {"temperature": 10.0}, **_CALENDAR},
    "dynamics.ode": {"world": {"infected": 0.0}, "types": {"person": {}},
                     "entities": {"person": {"type": "person", "count": 5}}},
    "pattern.product": {"inputs": {"skus": {"type": "table", "default": [{"sku": "a", "category": "tools",
                                                                           "base": 10}]}},
                        "mechanisms": {"trend": {"kind": "pattern", "mode": "trend", "start": 1, "rate": 0.01},
                                       "season": {"kind": "pattern", "mode": "draw", "dist": "normal", "mean": 1,
                                                  "sd": 0.1, "keys": ["tools"]}}},
    "pattern.sum": {"mechanisms": {"normal_temp": {"kind": "pattern", "mode": "cycle", "period": 4, "level": 10},
                                   "anomaly": {"kind": "pattern", "mode": "noise", "sd": 1}}},
    "pattern.trend": {"inputs": {"growth": {"type": "number", "default": 0.02}}},
    "pattern.seasonal": _CALENDAR,
    "pattern.calendar": _CALENDAR,
    "pattern.lifecycle": _CALENDAR,
    "pattern.step": _CALENDAR,
    "pattern.series": {**_CALENDAR, "inputs": {"weather": {"type": "table", "default": [
        {"date": "2025-12-20", "temp_c": 4}, {"date": "2025-12-22", "temp_c": 6}]}}},
    "pattern.draw": _SKUS,
    "pattern.segments": {"types": {"customer": {}}, "entities": {"customer": {"type": "customer", "count": 3}}},
    "pattern.diffusion": {"clock": {"rounds": 3, "unit": "day", "start": "2025-03-01"}},
    "pattern.elasticity": {"inputs": {"elasticity": {"type": "number", "default": -1.5}}},
    "pattern.carryover": {"world": {"ad_spend": 100.0}},
    "pattern.promotion": _SKUS,
    "pattern.reference_price": _SKUS,
    "pattern.habit": {"types": {"viewer": {"props": {"ads_seen": 1}}},
                      "entities": {"viewer": {"type": "viewer", "count": 2}}},
    "pattern.noise": {"types": {"resident": {}}, "entities": {"resident": {"type": "resident", "count": 2}}},
    "pattern.weather": _CALENDAR,
}


#: The hosts an example consults, bound for its check and run.
ADAPTERS = {"host.judge": {"judge": StubEvaluator}, "host.game_master": {"game_master": StubGameMaster}}


def _merge(into, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict) and key != "clock":
            _merge(into[key], value)
        else:
            into[key] = copy.deepcopy(value)


def _placed(spec):
    """The example of ``spec`` under the name the reference shows it with, in a contract of what it assumes."""
    example = spec.example
    contract = {"name": "Host", "clock": {"rounds": 3}, "types": {}, "entities": {},
                "mechanisms": {spec.name or f"my_{spec.mode}": copy.deepcopy(example)}}
    who = example.get("who")
    for kind in [who] if isinstance(who, str) else who or []:
        contract["types"][kind] = {"agent": True}
        contract["entities"][kind] = {"type": kind, "count": 2}
    _merge(contract, HOSTS.get(spec.key, {}))
    mechanisms = contract["mechanisms"]  # what the example builds on is declared before it
    name = spec.name or f"my_{spec.mode}"
    contract["mechanisms"] = {**{key: use for key, use in mechanisms.items() if key != name}, name: mechanisms[name]}
    return contract


MODES = [spec for family in FAMILIES.values() for spec in family.modes.values()]


def test_every_host_names_a_mode():
    assert set(HOSTS) <= {spec.key for spec in MODES}


@pytest.mark.parametrize("spec", MODES, ids=[spec.key for spec in MODES])
def test_every_reference_example_checks_clean_and_plays_three_rounds(spec):
    contract = _placed(spec)
    adapters = {name: make() for name, make in ADAPTERS.get(spec.key, {}).items()} or None
    errors = [str(issue) for issue in fg_env.check(contract, rounds=3, hosts=adapters) if issue.severity == "error"]
    assert errors == []
    result = fg_env.run(contract, seed=1, hosts=adapters)
    assert result.status != "failed", result.error
