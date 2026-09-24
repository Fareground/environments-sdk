"""Evaluation: a focal participant among background agents, paired with a baseline on the same seeds.

The expected scores come from the public-goods payoff worked by hand: each of four players starts every round
with 10, gives 0 (free ride) or 10 (cooperate), and the pot is multiplied and shared by all four.
"""
import json
import re

import pytest
from game_contracts import NIM

import fg_env
from fg_env.__main__ import main
from fg_env.analysis.runner import AnalysisError

ROUNDS = 3
PUBLIC_GOODS = {
    "name": "Public goods",
    "inputs": {"multiplier": {"type": "number", "default": 2}},
    "clock": {"rounds": ROUNDS},
    "world": {"pot": 0},
    "types": {"player": {"agent": True, "props": {"cash": 0}}},
    "entities": {f"p{i}": {"type": "player", "name": f"P{i}"} for i in range(4)},
    "actions": {"give": {"by": "player", "params": {"amount": {"type": "int", "min": 0, "max": 10}}, "terminal": True,
                         "do": ["$actor.cash += 10 - $params.amount", "$world.pot += $params.amount"]}},
    "stages": [{"name": "give", "turns": "simultaneous", "must_act": True}],
    "events": [{"phase": "end", "do": [{"each": "player", "do": ["$it.cash += $world.pot * $inputs.multiplier / 4"]},
                                       "$world.pot = 0"]}],
    "policies": {"free_ride": {"rules": [{"do": "give", "with": {"amount": 0}}]},
                 "cooperate": {"rules": [{"do": "give", "with": {"amount": 10}}]}},
    "outputs": {"cash": {"type": "map", "expr": "$dict(player, $it.id, $it.cash)"}},
}
SCORE = "$outputs.cash[$seat]"
MODES = {"resident": 0.75, "visitor": 0.25}


def _free_riding(suite=PUBLIC_GOODS, **options):
    settings = {"focal": "policy:free_ride", "background": "policy:cooperate", "score": SCORE, "modes": MODES,
                "runs": 3, **options}
    return fg_env.rl.evaluate(suite, **settings)


def test_focal_scores_and_paired_differences_match_the_hand_computed_payoffs():
    result = _free_riding()
    rows = {row["mode"]: row for row in result.scenarios}
    # resident: 3 free riders + 1 cooperator, pot 10 → 5 each: 10 + 5 a round. Baseline: all cooperate, 20 a round.
    assert rows["resident"]["focal_seats"] == 3 and rows["resident"]["focal"]["mean"] == 15 * ROUNDS
    assert rows["resident"]["baseline"]["mean"] == 20 * ROUNDS and rows["resident"]["difference"]["mean"] == -5 * ROUNDS
    # visitor: 1 free rider + 3 cooperators, pot 30 → 15 each: 10 + 15 a round.
    assert rows["visitor"]["focal_seats"] == 1 and rows["visitor"]["focal"]["mean"] == 25 * ROUNDS
    assert rows["visitor"]["difference"]["mean"] == 5 * ROUNDS and rows["visitor"]["clear"]
    assert rows["visitor"]["n"] == 3 and rows["visitor"]["unscored"] == 0
    assert result.modes["visitor"]["difference"]["mean"] == 15 and result.overall["n"] == 6
    for pair in result.pairs:
        assert len(pair["seats"]) == (3 if pair["mode"] == "resident" else 1)
        assert set(pair["seat_scores"]) == set(pair["seats"])
    assert "resident" in result.summary() and "clear" in result.summary()


def _nim_player(choose):
    def play(wake):
        stones = int(re.search(r"Stones left: (\d+)", wake.update).group(1))
        wake.call("take", {"count": choose(stones)})

    return play


def test_without_a_score_seats_are_scored_by_the_games_returns():
    optimal, one_at_a_time = _nim_player(lambda stones: stones % 4 or 1), _nim_player(lambda stones: 1)
    # Six stones, Ann first. Leaving a multiple of four wins (return +1); taking one each lets Bob take the last (−1).
    result = fg_env.rl.evaluate(NIM, focal=optimal, background=one_at_a_time, seats=["a"], inputs={"stones": 6}, runs=2)
    assert result.overall["focal"]["mean"] == 1 and result.overall["baseline"]["mean"] == -1
    assert result.overall["difference"]["mean"] == 2


def test_the_seats_are_drawn_from_the_seed_so_every_candidate_meets_the_same_draw():
    draws = [[pair["seats"] for pair in _free_riding(focal=focal, runs=5).pairs] for focal in
             ("policy:free_ride", "policy:cooperate", "random")]
    assert draws[0] == draws[1] == draws[2]
    assert len({tuple(seats) for seats in draws[0]}) > 1  # the draw moves between runs


def test_tags_and_held_out_scenarios_pool_their_run_pairs():
    suite = [{"contract": PUBLIC_GOODS, "name": "doubled", "tags": ["cooperation"]},
             {"contract": PUBLIC_GOODS, "name": "tripled", "inputs": {"multiplier": 3}, "tags": ["cooperation", "rich"],
              "held_out": True}]
    result = _free_riding(suite, modes={"visitor": 0.25}, runs=2)
    # tripled visitor: pot 30 × 3 / 4 = 22.5 → 32.5 a round; baseline pot 40 × 3 / 4 = 30 a round.
    assert result.splits["in_sample"]["difference"]["mean"] == 5 * ROUNDS
    assert result.splits["held_out"]["difference"]["mean"] == 2.5 * ROUNDS
    assert result.tags["cooperation"]["n"] == 4 and result.tags["rich"]["n"] == 2
    assert result.tags["rich"]["focal"]["mean"] == 32.5 * ROUNDS
    assert "Held out vs in sample" in result.summary() and "tripled (held out)" in result.summary()
    assert json.loads(json.dumps(result.to_dict()))["splits"]["held_out"]["n"] == 2


def test_cost_bills_the_focal_seats_turns_and_reported_tokens():
    def thrifty_model(wake):
        wake.record_usage(llm_calls=1, input_tokens=5, output_tokens=1)
        wake.call("give", {"amount": 0})

    result = _free_riding(focal=thrifty_model, modes={"visitor": 0.25}, runs=2)
    focal, baseline = result.overall["cost"]["focal"], result.overall["cost"]["baseline"]
    assert (focal["wakes"], focal["llm_calls"], focal["input_tokens"]) == (2 * ROUNDS, 2 * ROUNDS, 10 * ROUNDS)
    assert baseline["wakes"] == 2 * ROUNDS and baseline["input_tokens"] == 0
    assert result.focal == "thrifty_model" and result.overall["difference"]["mean"] == 5 * ROUNDS


def test_a_budget_caps_every_run():
    result = _free_riding(modes={"visitor": 0.25}, runs=1, budget={"calls": 1})
    # one simultaneous round is played before the budget is checked: free rider 10 + 15, cooperators 20.
    assert result.overall["focal"]["mean"] == 25 and result.overall["baseline"]["mean"] == 20


def test_a_run_that_fails_is_left_out_of_its_pair_and_noted():
    calls = []

    def fragile(wake):
        calls.append(wake.entity_id)
        if len(calls) == 1:
            raise RuntimeError("model unavailable")
        wake.call("give", {"amount": 0})

    result = _free_riding(focal=fragile, modes={"visitor": 0.25}, runs=2)
    assert result.overall["n"] == 1 and result.overall["unscored"] == 1
    assert "1 of 2 run pair(s) were left out" in result.notes[0] and "model unavailable" in result.notes[0]


def test_mistakes_in_a_suite_are_reported_before_anything_runs():
    with pytest.raises(ValueError, match="unknown field"):
        _free_riding([{"contract": PUBLIC_GOODS, "seat": "p0"}])
    with pytest.raises(ValueError,
                       match=r"scenario 'Public goods': seat 'p9' is not an agent the contract starts with"):
        _free_riding(seats=["p9"])
    with pytest.raises(ValueError, match="mode 'crowd' must be a share of the seats above 0 and at most 1"):
        _free_riding(modes={"crowd": 1.5})
    with pytest.raises(ValueError, match="scenario names must be unique; repeated: Public goods"):
        _free_riding([PUBLIC_GOODS, PUBLIC_GOODS])
    with pytest.raises(ValueError, match="background: unknown participant 'policy:nope'"):
        _free_riding(background="policy:nope")
    with pytest.raises(ValueError, match="focal is the participant being evaluated"):
        fg_env.rl.evaluate(PUBLIC_GOODS, focal=None)
    with pytest.raises(ValueError, match="budget has no 'money'"):
        _free_riding(budget={"money": 5})


def test_seats_may_be_a_type_and_every_unscorable_pair_is_an_error():
    with pytest.raises(AnalysisError, match="no run pair could be scored"):
        _free_riding(seats="player", score="$outputs.cash.missing")


def test_a_suite_file_reads_contracts_beside_it_and_the_cli_prints_the_summary(tmp_path, capsys):
    (tmp_path / "games").mkdir()
    (tmp_path / "games" / "goods.json").write_text(json.dumps(PUBLIC_GOODS))
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"scenarios": [{"contract": "games/goods.json", "tags": ["cooperation"]}]}))
    assert main(["evaluate", str(suite), "--focal", "policy:free_ride", "--background", "policy:cooperate",
                 "--score", SCORE, "--mode", "visitor=0.25", "--runs", "2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Evaluation of policy:free_ride: 1 scenario(s), 2 run(s) each") and "visitor" in out
    assert main(["evaluate", str(suite), "--focal", "policy:free_ride", "--mode", "visitor=many"]) == 1
    assert "--mode expects NAME=SHARE" in capsys.readouterr().err
