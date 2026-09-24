"""Combinatorial auctions: exact package winner determination, VCG payments, reserves, and the auction format."""
import itertools
import random

import pytest
from test_mech_markets import play, props, replies_of

import fg_env
from fg_env.errors import ContractError
from fg_env.expr import ExprError, compile_expr
from fg_env.mechanisms import auctions
from fg_env.mechanisms.package_auction import PackageBid, SearchLimit, best_allocation, settle


def _bids(*rows):
    return [PackageBid(bidder, tuple(items.split("+")), price) for bidder, items, price in rows]


def _brute_force(bids, reserves):
    best = 0.0
    for size in range(1, len(bids) + 1):
        for combo in itertools.combinations(bids, size):
            items = [item for bid in combo for item in bid.items]
            if len(set(items)) == len(items) and len({bid.bidder for bid in combo}) == len(combo):
                surplus = sum(bid.price - sum(reserves.get(i, 0) for i in bid.items) for bid in combo)
                if all(bid.price >= sum(reserves.get(i, 0) for i in bid.items) for bid in combo):
                    best = max(best, surplus)
    return best


def test_branch_and_bound_finds_the_same_best_surplus_as_trying_every_allocation():
    rng = random.Random(7)
    items = ["a", "b", "c", "d", "e"]
    for _ in range(60):
        bids = [PackageBid(f"p{rng.randrange(5)}", tuple(rng.sample(items, rng.randint(1, 3))), rng.randint(1, 40))
                for _ in range(rng.randint(1, 9))]
        reserves = {item: rng.choice([0, 0, 3]) for item in items}
        chosen, surplus = best_allocation(bids, reserves)
        assert surplus == pytest.approx(_brute_force(bids, reserves))
        won = [item for index in chosen for item in bids[index].items]
        assert len(won) == len(set(won)) and len({bids[i].bidder for i in chosen}) == len(chosen)


def test_vcg_charges_the_surplus_a_winner_displaces_and_never_less_than_the_reserves():
    bids = _bids(("a", "north+south", 60), ("b", "north", 35), ("b", "south", 10), ("c", "south", 30))
    winners, surplus = settle(bids, {"north": 5, "south": 5})
    assert surplus == 55
    assert [(w["bidder"], w["items"], w["pays"]) for w in winners] == [("b", ["north"], 30), ("c", ["south"], 25)]
    alone, _ = settle(_bids(("a", "x", 50)), {"x": 12})
    assert alone[0]["pays"] == 12  # no competition: the reserve
    first_price, _ = settle(bids, {"north": 5, "south": 5}, payment="pay_bid")
    assert [w["pays"] for w in first_price] == [35, 30]


def test_a_bid_at_exactly_the_reserve_wins_and_equal_surplus_favours_the_earlier_bid():
    winners, _ = settle(_bids(("a", "x", 10)), {"x": 10})
    assert [w["bidder"] for w in winners] == ["a"]
    tied, _ = settle(_bids(("a", "x", 20), ("b", "x", 20)), {})
    assert [(w["bidder"], w["pays"]) for w in tied] == [("a", 20)]


def test_search_beyond_its_budget_is_an_error_not_a_worse_answer():
    bids = [PackageBid(f"p{i}", (f"i{i % 12}", f"i{(i * 5 + 1) % 12}"), 10 + i % 3) for i in range(40)]
    with pytest.raises(SearchLimit):
        settle(bids, {}, budget=50)


def test_package_winners_function_reads_bids_as_data_and_explains_bad_ones():
    env = fg_env.load({"name": "x", "types": {"t": {}}, "clock": {"rounds": 1}})
    result = compile_expr("$package_winners([{bidder: a, items: [x, y], price: 10}, {bidder: b, items: [x], price: 6}, "
                          "{bidder: c, items: [y], price: 6}], 1)")(env.world.evaluation.scope())
    assert result == {"winners": [{"bidder": "b", "items": ["x"], "price": 6.0, "pays": 4.0},
                                  {"bidder": "c", "items": ["y"], "price": 6.0, "pays": 4.0}],
                      "surplus": 10.0, "revenue": 8.0}
    for source, message in [("$package_winners([{bidder: a, price: 3}])", "must be a map with bidder, items and price"),
                            ("$package_winners([{bidder: a, items: [x, x], price: 3}])", "distinct item names"),
                            ("$package_winners([{bidder: a, items: [x], price: 3}], 0, cheap)",
                             "payment must be one of")]:
        with pytest.raises(ExprError, match=message):
            compile_expr(source)(env.world.evaluation.scope())


def spectrum(**config):
    mechanism = {"kind": "market", "mode": "auction", "format": "combinatorial", "who": "bidder",
                 "item": "spectrum licences",
                 "items": ["north", "south", "east"], "reserve": 5, "reserves": {"east": 50}, **config}
    return {"name": "Spectrum", "clock": {"rounds": 3}, "types": {"bidder": {"agent": True, "props": {"cash": 100}}},
            "entities": {t: {"type": "bidder"} for t in "abc"}, "mechanisms": {"house": mechanism}}


def test_combinatorial_lot_sells_packages_at_vcg_prices_refunds_escrow_and_keeps_unsold_items():
    assert not [i for i in fg_env.check(spectrum()) if i.severity == "error"]
    env, replies = play(spectrum(), {
        (1, "a"): [("house_bid", {"package": ["north", "south"], "price": 60})],
        (1, "b"): [("house_bid", {"package": ["north"], "price": 20}),
                   ("house_bid", {"package": ["north"], "price": 35}),
                   ("house_bid", {"package": ["south"], "price": 10})],
        (1, "c"): [("house_bid", {"package": ["south"], "price": 30}),
                   ("house_bid", {"package": ["east"], "price": 40})],
    })
    assert [r.ok for r in replies_of(replies, "b")] == [True, True, True]  # the second bid replaced the first
    below_reserve = replies_of(replies, "c")[1]
    assert not below_reserve.ok and "at least 50" in below_reserve.text
    assert (props(env, "b")["cash"], props(env, "b")["house_items"]) == (70, ["north"])
    assert (props(env, "c")["cash"], props(env, "c")["house_items"]) == (75, ["south"])
    assert props(env, "a")["cash"] == 100 and all(props(env, t)["house_escrow"] == 0 for t in "abc")
    assert env.props["house_revenue"] == 55 and env.props["house_items"] == ["east"]
    results = env.world.records("house_results")
    assert [(r["winner"], r["items"], r["price"]) for r in results] == [("b", ["north"], 30), ("c", ["south"], 25)]
    assert not auctions.audit(env.world, "house")
    state = compile_expr("$auction(house)")(env.world.evaluation.scope())
    assert state["items"] == ["east"] and state["sold"] == 2


def test_package_bids_are_escrowed_at_the_highest_one_and_limited_per_bidder():
    contract = spectrum(packages=2)
    contract["stages"] = [{"name": "bidding", "turns": "sequential", "max_actions": 5, "max_calls": 10}]
    contract["mechanisms"]["house"]["stage"] = "bidding"
    env = fg_env.load(contract, seed=1)
    seen = {}

    def bid(wake):
        if wake.entity_id == "a":
            for package, price in ((["north"], 30), (["south"], 45), (["north", "south"], 70), (["west"], 10)):
                seen[tuple(package)] = wake.call("house_bid", {"package": package, "price": price})
            seen["escrow"] = props(env, "a")["house_escrow"]
        wake.end()

    env.run(bid, rounds=1)
    assert seen[("north",)].ok and seen[("south",)].ok and seen["escrow"] == 45
    assert not seen[("north", "south")].ok and "already hold 2 package bids" in seen[("north", "south")].text
    assert not seen[("west",)].ok
    assert (props(env, "a")["cash"] == 95 and props(env, "a")["house_items"]
            == ["south"])  # won south alone at the reserve
    assert not auctions.audit(env.world, "house")


def test_combinatorial_config_errors_say_what_to_fix():
    for config, message in [({"items": []}, "needs `items`"), ({"items": ["x", "x"]}, "distinct"),
                            ({"reserves": {"west": 3}}, "not for sale: west"),
                            ({"items": [f"i{n}" for n in range(30)], "reserves": {}}, "at most 24 items")]:
        with pytest.raises(ContractError, match=message):
            fg_env.load(spectrum(**config))
    with pytest.raises(ContractError, match="belong to a combinatorial auction"):
        fg_env.load(spectrum(format="first_price", reserves={}))
