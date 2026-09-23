"""Mechanisms against results worked out by hand: market makers, ballots and cascades give the textbook numbers.

(The M/M/c queue against Erlang C, deferred acceptance against brute-force stable matchings and side pots are held to
theory in test_ops_queue.py, test_matching.py and test_mech_cards.py.)"""
import math
import statistics

import pytest

import fg_env


def _prediction(maker, buy):
    contract = {"name": "Forecast", "clock": {"rounds": 3}, "types": {"f": {"agent": True, "props": {"cash": 1000}}},
                "entities": {"f1": {"type": "f"}, "f2": {"type": "f"}},
                "mechanisms": {"m": {"kind": "market", "mode": "prediction", "who": "f", "outcomes": ["yes", "no"],
                                     "maker": maker, "liquidity": 100, "resolve_at": 2, "outcome": "yes"}},
                "outputs": {"price": "$amm('m').prices.yes", "cash": "$entity(f1).cash", "held": "$entity(f1).m_shares.yes"}}
    env = fg_env.load(contract, seed=1)

    def trade(wake):
        if wake.entity_id == "f1" and wake.round == 1:
            assert wake.call("m_buy", {"outcome": "yes", **buy}).ok
        wake.end()

    env.run(trade, rounds=1)
    return env.result().outputs


def test_lmsr_buying_50_shares_costs_b_ln_of_the_exponentials_and_moves_the_price_to_the_logistic():
    out = _prediction("lmsr", {"shares": 50})
    assert 1000 - out["cash"] == pytest.approx(100 * math.log((math.exp(0.5) + 1) / 2), abs=1e-6)  # 28.093
    assert out["price"] == pytest.approx(math.exp(0.5) / (math.exp(0.5) + 1), abs=1e-6)  # 0.6225


def test_cpmm_spending_20_on_a_100_100_pool_buys_what_keeps_the_product_constant():
    out = _prediction("cpmm", {"spend": 20})
    assert out["held"] == pytest.approx(120 - 100 * 100 / 120, abs=1e-6)  # 36.667
    assert out["cash"] == pytest.approx(980)


# 5 voters a > b > c, 4 voters b > c > a, 3 voters c > b > a: plurality elects a, every other rule b.
PROFILE = [["a", "b", "c"]] * 5 + [["b", "c", "a"]] * 4 + [["c", "b", "a"]] * 3


@pytest.mark.parametrize("method, winner", [("plurality", "a"), ("ranked", "b"), ("borda", "b"), ("condorcet", "b")])
def test_one_ranked_profile_elects_the_textbook_winner_under_each_rule(method, winner):
    contract = {"name": "Vote", "clock": {"rounds": 1}, "types": {"m": {"agent": True}},
                "population": [{"type": "m", "count": len(PROFILE)}],
                "mechanisms": {"v": {"kind": "decision", "mode": "ballot", "who": "m", "options": ["a", "b", "c"],
                                     "method": method}}}
    env = fg_env.load(contract, seed=1)

    def vote(wake):
        ranking = PROFILE[int(wake.entity_id.rsplit("_", 1)[1]) - 1]
        wake.call("v_vote", {"choice": ranking[0]} if method == "plurality" else {"choices": ranking})
        wake.end()

    env.run(vote, rounds=1)
    result = env.props["v_result"]
    assert result["winner"] == winner
    if method == "borda":
        assert result["counts"] == {"a": 10, "b": 16, "c": 10}
    if method == "ranked":
        assert len(result["rounds"]) == 2  # c is eliminated and its voters carry b past a, 7 to 5


def _star(leaves, rounds, persistent, seed):
    contract = {"name": "Star", "clock": {"rounds": rounds}, "types": {"u": {}}, "population": [{"type": "u", "count": leaves + 1}],
                "relations": {"k": {"symmetric": True}}, "links": [{"relation": "k", "among": "u", "graph": "star"}],
                "mechanisms": {"s": {"kind": "social", "mode": "diffusion", "who": "u", "over": "k", "model": "cascade",
                                     "p": 0.3, "persistent": persistent, "seeds": {"x": ["u_1"]}}},
                "outputs": {"reach": "$adopters(x)"}}
    return fg_env.run(contract, None, seed=seed).outputs["reach"]


def test_a_one_shot_cascade_from_a_hub_reaches_the_binomial_expectation():
    leaves, seeds = 500, 6
    reach = [_star(leaves, 3, False, seed) for seed in range(seeds)]
    spread = math.sqrt(leaves * 0.3 * 0.7) / math.sqrt(seeds)  # the mean's standard error: 10.2 / sqrt(seeds)
    assert statistics.mean(reach) == pytest.approx(1 + leaves * 0.3, abs=4 * spread)  # 151
    assert len(set(reach)) > 1  # seeded draws, not a fixed count


def test_a_persistent_hub_retries_every_step_so_each_leaf_adopts_with_one_minus_seven_tenths_cubed():
    leaves, seeds = 200, 6
    reach = [_star(leaves, 3, True, seed) for seed in range(seeds)]
    p = 1 - 0.7 ** 3
    assert statistics.mean(reach) == pytest.approx(1 + leaves * p, abs=4 * math.sqrt(leaves * p * (1 - p) / seeds))  # 132.4
