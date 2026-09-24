"""Tournaments: schedules, ratings and rankings against hand-computed values, scoring, and whole tournaments.

Expected numbers come from the formulas written beside them (Glickman's published example, closed-form
two-player fits, equilibria of small games worked by hand), never from the code under test.
"""
import json
import math

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.analysis.runner import AnalysisError, run_seeds
from fg_env.runtime.measure import RunResult
from fg_env.tournament import Glicko, alpha_rank, elo_mle, glicko2_period, nash_average, schulze, tournament
from fg_env.tournament.scoring import SeatScorer
from fg_env.tournament.seating import schedule, swiss_round

HANDS = ["rock", "paper", "scissors"]


def _duel(name, choice, values, winner):
    """Two seats submit a sealed choice; `winner` names the seat that won (or null for a draw)."""
    return {
        "name": name,
        "clock": {"rounds": 1},
        "world": {"left": {"type": "any", "default": None}, "right": {"type": "any", "default": None}},
        "types": {"player": {"agent": True, "props": {}}},
        "entities": {"left": {"type": "player", "name": "Lefty"}, "right": {"type": "player", "name": "Righty"}},
        "actions": {"pick": {"by": "player", "params": {"choice": choice}, "terminal": True,
                             "do": [{"if": "$actor.id == left", "then": ["$world.left = $params.choice"],
                                     "else": ["$world.right = $params.choice"]}]}},
        "stages": [{"name": "pick", "turns": "simultaneous", "must_act": True}],
        "policies": {str(v): {"rules": [{"do": "pick", "with": {"choice": v}}]} for v in values},
        "end": [{"name": "decided", "when": "$world.left != null and $world.right != null", "winner": winner}],
        "outputs": {"points": {"type": "map", "expr": "{left: $world.left, right: $world.right}"}},
    }


BEATS = "($a == rock and $b == scissors) or ($a == paper and $b == rock) or ($a == scissors and $b == paper)"
RPS = _duel("Rock paper scissors", {"type": "enum", "values": HANDS}, HANDS,
            "'left' if $beats($world.left, $world.right) else ('right' if $beats($world.right, $world.left) else null)")
RPS["defs"] = {"beats": {"args": ["a", "b"], "expr": BEATS}}
NUMBERS = _duel("Number duel", {"type": "int", "min": 1, "max": 5}, [1, 2, 3, 4, 5],
                "'left' if $world.left > $world.right else ('right' if $world.right > $world.left else null)")


# --- schedules ----------------------------------------------------------------------------------------------


def test_round_robin_rotates_every_group_and_all_play_all_uses_every_order():
    assert schedule(["a", "b", "c"], 2, "round_robin") == [("a", "b"), ("b", "a"), ("a", "c"), ("c", "a"),
                                                          ("b", "c"), ("c", "b")]
    assert schedule(["a", "b", "c"], 3, "round_robin") == [("a", "b", "c"), ("b", "c", "a"), ("c", "a", "b")]
    assert len(schedule(["a", "b", "c"], 3, "all_play_all")) == 6  # 3! orders
    assert len(schedule(["a", "b", "c", "d"], 3, "all_play_all")) == 24  # 4 groups × 3!


def test_swiss_round_pairs_by_rank_avoids_rematches_and_rests_the_lowest_without_a_bye():
    assert swiss_round(["a", "b", "c", "d", "e"], 2, {}, {}) == ([("a", "b"), ("c", "d")], ["e"])
    tables, resting = swiss_round(["a", "b", "c", "d", "e"], 2, {frozenset("ab"): 1}, {"e": 1})
    assert resting == ["d"] and tables == [("a", "c"), ("b", "e")]


# --- ratings and rankings --------------------------------------------------------------------------------------


def test_glicko2_matches_glickmans_published_example():
    players = {"p": Glicko(1500, 200, 0.06), "a": Glicko(1400, 30), "b": Glicko(1550, 100), "c": Glicko(1700, 300)}
    rated = glicko2_period(players, [("p", "a", 1.0), ("p", "b", 0.0), ("p", "c", 0.0)])["p"]
    assert rated.rating == pytest.approx(1464.06, abs=0.01)
    assert rated.rd == pytest.approx(151.52, abs=0.01)
    assert rated.volatility == pytest.approx(0.05999, abs=1e-5)


def test_maximum_likelihood_elo_of_two_players_matches_the_closed_form():
    # A: 3 wins, 1 loss, 1 draw against B; each carries one virtual draw against a 1500 anchor.
    # By symmetry θ_A = −θ_B = t solves (3 + ½) − 5σ(2t) + (½ − σ(t)) = 0.
    def sigmoid(x):
        return 1 / (1 + math.exp(-x))

    low, high = 0.0, 5.0
    for _ in range(100):
        t = (low + high) / 2
        low, high = (t, high) if 3.5 - 5 * sigmoid(2 * t) + 0.5 - sigmoid(t) > 0 else (low, t)
    a, c = 5 * sigmoid(2 * t) * (1 - sigmoid(2 * t)), sigmoid(t) * (1 - sigmoid(t))
    se = math.sqrt((a + c) / (c * (2 * a + c))) * 400 / math.log(10)  # diagonal of the inverse 2×2 curvature
    fit = elo_mle(["A", "B"], [("A", "B", 1.0)] * 3 + [("A", "B", 0.0), ("B", "A", 0.5)])
    assert fit["A"].rating == pytest.approx(1500 + t * 400 / math.log(10), abs=1e-6)
    assert fit["B"].rating == pytest.approx(1500 - t * 400 / math.log(10), abs=1e-6)
    assert fit["A"].se == pytest.approx(se, rel=1e-6)
    assert fit["A"].low == pytest.approx(fit["A"].rating - 1.959964 * se, rel=1e-6)


def test_an_unbeaten_entrant_gets_a_finite_rating_with_a_wide_interval():
    fit = elo_mle(["A", "B"], [("A", "B", 1.0)] * 4)
    assert math.isfinite(fit["A"].rating) and fit["A"].rating > 1700 and fit["A"].high - fit["A"].low > 400


def test_nash_average_of_small_games_worked_by_hand():
    rps = [[0, 1, -1], [-1, 0, 1], [1, -1, 0]]
    result = nash_average(["r", "p", "s"], rps)
    assert result["equilibrium"] == {k: pytest.approx(1 / 3) for k in "rps"}
    assert result["rating"] == {k: pytest.approx(0.0, abs=1e-9) for k in "rps"}
    transitive = nash_average(["a", "b", "c"], [[0, 1, 1], [-1, 0, 1], [-1, -1, 0]])
    assert transitive["equilibrium"] == {"a": pytest.approx(1.0), "b": pytest.approx(0.0), "c": pytest.approx(0.0)}
    assert transitive["rating"] == {"a": pytest.approx(0.0), "b": pytest.approx(-1.0), "c": pytest.approx(-1.0)}
    # A copy of rock shares rock's weight; nothing else changes (the property that motivates Nash averaging).
    cloned = nash_average(["R", "R2", "P", "S"], [[0, 0, -1, 1], [0, 0, -1, 1], [1, 1, 0, -1], [-1, -1, 1, 0]])
    assert cloned["equilibrium"] == {"R": pytest.approx(1 / 6), "R2": pytest.approx(1 / 6), "P": pytest.approx(1 / 3),
                                     "S": pytest.approx(1 / 3)}


def test_alpha_rank_of_two_entrants_follows_the_fixation_probabilities():
    # a beats b by margin 1, so Δ = ±2. ρ(b→a) = (1 − e^(−2α)) / (1 − e^(−2mα)), ρ(a→b) = (1 − e^(2α)) / (1 − e^(2mα)).
    alpha, m = 0.1, 5
    to_a = (1 - math.exp(-2 * alpha)) / (1 - math.exp(-2 * m * alpha))
    to_b = (1 - math.exp(2 * alpha)) / (1 - math.exp(2 * m * alpha))
    mass = alpha_rank(["a", "b"], [[0, 1], [-1, 0]], alpha=alpha, population=m)
    assert mass["a"] == pytest.approx(to_a / (to_a + to_b)) and mass["b"] == pytest.approx(to_b / (to_a + to_b))
    assert alpha_rank(["r", "p", "s"], [[0, 1, -1], [-1, 0, 1], [1, -1, 0]]) == {k: pytest.approx(1 / 3) for k in "rps"}


def test_schulze_follows_the_strongest_paths():
    # a>b 4:2, b>c 5:1, a–c 3:3. Strongest paths: a→c through b is min(4, 5) = 4 > 0, so a, b, c.
    preferred = {"a": {"b": 4, "c": 3}, "b": {"a": 2, "c": 5}, "c": {"a": 3, "b": 1}}
    assert [(r["rank"], r["entrant"]) for r in schulze(["a", "b", "c"], preferred)] == [(1, "a"), (2, "b"), (3, "c")]
    cycle = {"a": {"b": 1, "c": 0}, "b": {"a": 0, "c": 1}, "c": {"a": 1, "b": 0}}
    assert [r["rank"] for r in schulze(["a", "b", "c"], cycle)] == [1, 1, 1]


# --- scoring ------------------------------------------------------------------------------------------------------


def _result(**fields):
    base = dict(status="ended", ended_by="decided", rounds=1, seed=1, arm=None, inputs={}, outputs={}, metrics={},
                series={})
    return RunResult(**{**base, **fields})


def test_seat_scorer_reads_winners_outputs_expressions_and_functions():
    contract = fg_env.parse(RPS)
    agents = {"left": "Lefty", "right": "Righty", "house": "House"}
    winner = SeatScorer(contract, None, ["left", "right"], agents)
    assert winner(_result(winner="Righty")) == ({"left": 0.0, "right": 1.0}, "")
    assert winner(_result(winner=["left", "right"])) == ({"left": 1.0, "right": 1.0}, "")
    assert winner(_result(winner="house")) == ({"left": 0.0, "right": 0.0}, "")  # an unseated agent won
    scores, reason = winner(_result(winner="wolves"))
    assert scores is None and "not a seat" in reason
    output = SeatScorer(contract, "points", ["left", "right"], agents)
    assert output(_result(outputs={"points": {"Lefty": 2, "right": True}})) == ({"left": 2.0, "right": 1.0}, "")
    expression = SeatScorer(contract, "$outputs.points[$seat] * 10", ["left", "right"], agents)
    assert expression(_result(outputs={"points": {"left": 1, "right": 3}})) == ({"left": 10.0, "right": 30.0}, "")
    function = SeatScorer(contract, lambda result, seat: len(seat), ["left", "right"], agents)
    assert function(_result()) == ({"left": 4.0, "right": 5.0}, "")
    with pytest.raises(ValueError, match="not a declared output"):
        SeatScorer(contract, "nope", ["left", "right"], agents)
    with pytest.raises(ValueError, match="score:"):
        SeatScorer(contract, "$outputs.points[", ["left", "right"], agents)


# --- whole tournaments -------------------------------------------------------------------------------------------


def test_rock_paper_scissors_round_robin_is_a_perfect_cycle():
    entrants = {hand: f"policy:{hand}" for hand in HANDS}
    result = tournament(RPS, entrants, games=2, seed=4)
    # Each pair meets in 2 seatings × 2 games; each hand beats one opponent 4 times and loses to the other 4 times.
    for row in result.standings:
        assert (row["wins"], row["draws"], row["losses"], row["played"]) == (4, 0, 4, 8)
        assert row["elo"]["rating"] == pytest.approx(1500, abs=1e-6)
    assert result.head_to_head["paper"]["rock"] == {"wins": 4, "draws": 0, "losses": 0}
    assert result.evaluation["margins"]["rock"] == {"rock": 0.0, "paper": -1.0, "scissors": 1.0}
    assert result.evaluation["nash_average"]["equilibrium"] == {hand: pytest.approx(1 / 3) for hand in HANDS}
    assert result.evaluation["alpha_rank"] == {hand: pytest.approx(1 / 3) for hand in HANDS}
    assert [row["rank"] for row in result.evaluation["votes"]] == [1, 1, 1]
    assert result.returns["rock"]["left"]["mean"] == pytest.approx(0.5)  # beats scissors, loses to paper, in each seat
    games = [g for g in result.games if "seating" in g]
    assert len(games) == 12 and {g["seed"] for g in games} == set(run_seeds(4, 2))  # duplicate seeds
    assert result.standing("rock")["cost"]["wakes"] == 8 and result.standing("rock")["cost"]["actions"] == 8
    text = result.summary()
    assert "Nash average" in text and "α-Rank" in text and "Head to head" in text and "Cost per entrant" in text


def test_a_transitive_field_is_ranked_in_order_by_every_method():
    entrants = {f"n{v}": f"policy:{v}" for v in (3, 1, 5, 2, 4)}
    result = tournament(NUMBERS, entrants, seed=2)
    order = [row["entrant"] for row in result.standings]
    assert order == ["n5", "n4", "n3", "n2", "n1"]
    # The ladder is symmetric about n3: n3 sits at the anchor, and n5/n1 and n4/n2 mirror each other.
    elo = {row["entrant"]: row["elo"] for row in result.standings}
    assert elo["n3"]["rating"] == pytest.approx(1500, abs=1e-6)
    for high, low in (("n5", "n1"), ("n4", "n2")):
        assert elo[high]["rating"] + elo[low]["rating"] == pytest.approx(3000, abs=1e-6)
        assert elo[high]["se"] == pytest.approx(elo[low]["se"], rel=1e-9)
    assert result.evaluation["nash_average"]["equilibrium"]["n5"] == pytest.approx(1.0)
    assert max(result.evaluation["alpha_rank"], key=result.evaluation["alpha_rank"].get) == "n5"
    assert [r["entrant"] for r in result.evaluation["votes"]] == order
    glicko = tournament(NUMBERS, entrants, seed=2, rating="glicko2")
    assert [row["entrant"] for row in glicko.standings] == order


def test_swiss_gives_one_bye_per_round_to_different_entrants_and_ranks_the_strongest_first():
    entrants = {f"n{v}": f"policy:{v}" for v in (1, 2, 3, 4, 5)}
    result = tournament(NUMBERS, entrants, pairing="swiss", seed=3)
    byes = [g["bye"] for g in result.games if "bye" in g]
    assert len(byes) == 3 == len(set(byes))  # ⌈log₂ 5⌉ rounds, a different entrant rests each time
    assert result.standings[0]["entrant"] == "n5" and result.standings[0]["losses"] == 0
    first_round = [tuple(g["seating"].values()) for g in result.games if g.get("round") == 0 and "seating" in g]
    second_round = [frozenset(g["seating"].values()) for g in result.games if g.get("round") == 1 and "seating" in g]
    assert not {frozenset(t) for t in first_round} & set(second_round)  # no rematch when one can be avoided


def test_score_expression_ranks_seats_and_results_do_not_depend_on_workers():
    entrants = {"one": "policy:1", "three": "policy:3", "five": "policy:5"}
    serial = tournament(NUMBERS, entrants, games=2, score="$outputs.points[$seat]")
    parallel = tournament(NUMBERS, entrants, games=2, score="$outputs.points[$seat]", workers=2)
    assert serial.standings == parallel.standings and serial.evaluation == parallel.evaluation
    assert serial.standing("five")["score"]["mean"] == pytest.approx(5.0)
    assert serial.score == "expression $outputs.points[$seat]"


def test_failing_entrants_are_reported_and_a_tournament_with_no_completed_game_raises():
    def broken(wake):
        raise RuntimeError("model offline")

    result = tournament(NUMBERS, {"ok": "policy:2", "flaky": broken, "other": "policy:4"})
    assert result.standing("flaky")["failed"] == 4 and result.standing("ok")["played"] == 2
    assert any("failed and were left out" in n and "model offline" in n for n in result.notes)
    with pytest.raises(AnalysisError, match="all 2 game"):
        tournament(NUMBERS, {"a": broken, "b": broken})


def test_callable_entrants_are_billed_for_their_model_usage():
    def spender(wake):
        wake.record_usage(llm_calls=1, input_tokens=50, output_tokens=5)
        wake.call("pick", {"choice": 3})

    result = tournament(NUMBERS, {"llm": spender, "bot": "policy:1"}, games=2)
    assert result.standing("llm")["cost"]["input_tokens"] == 200  # 2 seatings × 2 games × 50
    assert result.standing("bot")["cost"]["input_tokens"] == 0
    assert "tokens in" in result.summary()


@pytest.mark.parametrize("kwargs, message", [
    ({"entrants": {"solo": "random"}}, "at least two entrants"),
    ({"pairing": "knockout"}, "pairing must be"),
    ({"rating": "trueskill"}, "rating must be"),
    ({"seats": ["left", "middle"]}, "not an agent entity"),
    ({"seats": ["left"]}, "at least two seats"),
    ({"entrants": {"a": "policy:nope", "b": "random"}}, "entrant 'a'"),
    ({"swiss_rounds": 2}, "only to pairing='swiss'"),
    ({"score": "missing"}, "not a declared output"),
])
def test_tournament_mistakes_say_what_to_fix(kwargs, message):
    arguments = {"entrants": {"a": "policy:1", "b": "policy:2"}, **kwargs}
    with pytest.raises(ValueError, match=message):
        tournament(NUMBERS, arguments.pop("entrants"), **arguments)


def test_cli_tournament_prints_a_summary_or_json(tmp_path, capsys):
    path = tmp_path / "rps.json"
    path.write_text(json.dumps(RPS))
    argv = ["tournament", str(path)] + [f"--entrant={hand}=policy:{hand}" for hand in HANDS]
    assert main(argv + ["--games", "1"]) == 0
    assert "Schulze vote" in capsys.readouterr().out
    assert main(argv + ["--json", "--pairing", "all_play_all"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pairing"] == "all_play_all" and len(data["standings"]) == 3
    assert main(["tournament", str(path), "--entrant", "rock"]) == 1
    assert "NAME=PARTICIPANT" in capsys.readouterr().err
