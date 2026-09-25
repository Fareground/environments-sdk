"""Game algorithms as participants: "mcts:N", "ismcts:N", "minimax[:D]", "cfr:<file>" and "cfr:<iterations>"."""
import pytest
from game_contracts import GAMES

import fg_env
from fg_env.game.algorithms import CFRSolver, TabularPolicy

NIM = GAMES / "nim.json"
KUHN = GAMES / "kuhn_poker.json"
TIC_TAC_TOE = GAMES / "tic_tac_toe.json"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_minimax_wins_nim_from_a_winning_position_against_random(seed):
    result = fg_env.run(NIM, {"a": "minimax", "b": "random"}, seed=seed)
    assert result.ok and result.returns == {"a": 1.0, "b": -1.0}


def test_mcts_does_not_lose_tic_tac_toe_to_random_and_replays_exactly():
    outcomes = [fg_env.run(TIC_TAC_TOE, {"x": "mcts:40", "o": "random"}, seed=seed) for seed in (1, 2)]
    assert all(result.ok and result.returns["x"] >= 0 for result in outcomes)
    again = fg_env.run(TIC_TAC_TOE, {"x": "mcts:40", "o": "random"}, seed=1)
    assert again.events == outcomes[0].events


def test_search_participants_refuse_games_with_hidden_information():
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(KUHN, {"p0": "mcts:10", "p1": "random"}, seed=1)
    result = failed.value.result
    assert result.status == "failed" and "hidden information" in result.error


def test_a_saved_cfr_policy_plays_its_seat_and_a_foreign_policy_is_refused(tmp_path):
    game = fg_env.rl.game(KUHN)
    policy = CFRSolver(game, plus=True).iterate(300).average_policy()
    path = tmp_path / "kuhn.json"
    policy.save(path)
    assert TabularPolicy.load(path).table == policy.table
    played = fg_env.run(KUHN, {"p0": f"cfr:{path}", "p1": "cfr:300"}, seed=4)
    assert played.ok and sum(played.returns.values()) == 0
    foreign = tmp_path / "foreign.json"
    TabularPolicy({"not-a-kuhn-state": {"bet": 1.0}}).save(foreign)
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(KUHN, {"p0": f"cfr:{foreign}", "p1": "random"}, seed=4)
    refused = failed.value.result
    assert refused.status == "failed" and "no entry" in refused.error


def test_a_cfr_policy_chooses_by_the_information_state_the_game_tree_uses():
    game = fg_env.rl.game(KUHN)
    tree_policy = CFRSolver(game, plus=True).iterate(200).average_policy()
    always_pass = TabularPolicy({key: {text: (1.0 if text == "pass" else 0.0) for text in value}
                                 for key, value in tree_policy.table.items()})
    chosen = []

    def watch(event):
        if event["kind"] == "action":
            chosen.append(event["data"]["action"])

    env = fg_env.load(KUHN, seed=2)
    from fg_env.game.algorithms.participants import PolicyPlayer

    player = PolicyPlayer.__new__(PolicyPlayer)
    player.__init__("1", 0)
    player._policy = always_pass
    env.run({"p0": player, "p1": player}, on_event=watch)
    assert chosen and set(chosen) == {"pass"}


def test_ismcts_plays_kuhn_from_its_own_view_of_the_log():
    result = fg_env.run(KUHN, {"p0": "ismcts:15", "p1": "random"}, seed=5)
    assert result.ok and result.returns["p0"] in (-2.0, -1.0, 1.0, 2.0)


def test_unknown_algorithm_arguments_say_what_to_write():
    with pytest.raises(ValueError, match="whole number"):
        fg_env.run(NIM, {"a": "mcts:many", "b": "random"}, seed=1)
    with pytest.raises(ValueError, match="cfr needs"):
        fg_env.run(NIM, {"a": "cfr", "b": "random"}, seed=1)


LUCKY = {"name": "Safe or risky", "clock": {"rounds": 1},
         "types": {"p": {"agent": True, "props": {"pts": 0},
                         "score": {"value": "$it.pts * 2 - $sum(p, $it.pts)", "utility": "zero_sum"}}},
         "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
         "stages": [{"name": "s", "must_act": True}],
         "actions": {"safe": {"by": "p", "do": "$actor.pts += 1", "terminal": True},
                     "risky": {"by": "p", "do": "$actor.pts += 3 if $chance(0.5) else -3", "terminal": True}},
         "outputs": {"pts": "$entity(a).pts"}}


@pytest.mark.parametrize("bot", ["minimax", "mcts:8"])
def test_search_participants_do_not_see_the_runs_future_luck(bot):
    """A draw in an expression is no chance node the search weighs: its copy of the run draws fresh luck, so its risky
    bets lose as a fair coin does (with the run's own streams it took the bet exactly when it would win)."""
    points = [fg_env.run(LUCKY, {"a": bot, "b": "idle"}, seed=seed).outputs["pts"] for seed in range(20)]
    assert points.count(-3) >= 2, points
