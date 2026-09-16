"""Search and solving algorithms over games, checked against known answers.

The slower verifications (a whole tic-tac-toe solve, Leduc poker's uniform-policy exploitability) run only with
FG_ENV_SLOW=1: FG_ENV_SLOW=1 pytest tests/sdk/test_game_algorithms.py
"""
import os
import random

import pytest

import fg_env
from fg_env.sdk.game.algorithms import (CFRSolver, ISMCTSBot, MCTSBot, TabularPolicy, best_response, determinize,
                                        exploitability, extract_tree, minimax, nash_conv, policy_values)
from game_contracts import GAMES

slow = pytest.mark.skipif(not os.environ.get("FG_ENV_SLOW"), reason="slow verification: set FG_ENV_SLOW=1")


def _game(name, **kwargs):
    return fg_env.game(GAMES / f"{name}.json", **kwargs)


@pytest.fixture(scope="module")
def kuhn_tree():
    return extract_tree(_game("kuhn_poker"))


def _uniform(tree):
    return TabularPolicy({key: {text: 1 / len(texts) for text in texts} for key, (_, texts) in tree.infosets().items()})


def test_kuhn_tree_has_the_known_size_and_the_uniform_policy_its_known_exploitability(kuhn_tree):
    assert kuhn_tree.nodes == 58 and len(kuhn_tree.infosets()) == 12
    assert exploitability(kuhn_tree, _uniform(kuhn_tree)) == pytest.approx(0.458333, abs=1e-6)  # OpenSpiel's value


@pytest.mark.parametrize("plus", [False, True], ids=["cfr", "cfr_plus"])
def test_cfr_reaches_the_kuhn_game_value_with_near_zero_exploitability(kuhn_tree, plus):
    policy = CFRSolver(kuhn_tree, plus=plus).iterate(1000).average_policy()
    values = policy_values(kuhn_tree, policy)
    assert values[0] == pytest.approx(-1 / 18, abs=2e-3) and sum(values) == pytest.approx(0, abs=1e-12)
    assert exploitability(kuhn_tree, policy) < 0.01


def test_a_best_response_to_an_always_betting_opponent_exploits_it(kuhn_tree):
    always_bet = TabularPolicy({key: {text: 1.0 if text == "bet" else 0.0 for text in texts}
                                for key, (_, texts) in kuhn_tree.infosets().items()})
    response = best_response(kuhn_tree, always_bet, 1)
    mixed = TabularPolicy({**always_bet.table, **{key: value for key, value in response.table.items()}})
    assert policy_values(kuhn_tree, mixed)[1] > policy_values(kuhn_tree, always_bet)[1]


@pytest.mark.parametrize("name, heads, value", [("matching_pennies", 1 / 2, 0.0), ("biased_pennies", 1 / 3, 1 / 3)])
def test_cfr_finds_the_mixed_equilibrium_of_zero_sum_matrix_games(name, heads, value):
    tree = extract_tree(_game(name).as_turn_based())
    policy = CFRSolver(tree, plus=True).iterate(2000).average_policy()
    for distribution in policy.table.values():
        assert distribution["show(side=heads)"] == pytest.approx(heads, abs=0.02)
    assert policy_values(tree, policy)[0] == pytest.approx(value, abs=0.01)
    assert exploitability(tree, policy) < 0.02


@pytest.mark.parametrize("stones, value", [(4, -1.0), (5, 1.0), (8, -1.0), (9, 1.0)])
def test_minimax_gives_nims_known_values(stones, value):
    state = _game("nim", inputs={"stones": stones}).new_initial_state()
    result, best = minimax(state)
    assert result == value
    if value > 0:
        assert best.args["count"] == stones % 4  # leave a multiple of four


def test_minimax_finds_the_tic_tac_toe_draw_and_refuses_hidden_information():
    state = _game("tic_tac_toe").new_initial_state()
    for cell in (4, 0):
        state.apply_action({"tool": "mark", "args": {"cell": cell}})
    assert minimax(state)[0] == 0.0
    with pytest.raises(ValueError, match="hidden information"):
        minimax(_game("kuhn_poker").new_initial_state())


@slow
def test_tic_tac_toe_is_a_draw_from_the_empty_board():
    assert minimax(_game("tic_tac_toe").new_initial_state())[0] == 0.0


@slow
def test_leduc_uniform_policy_has_openspiels_nash_conv():
    tree = extract_tree(_game("leduc_poker"))
    assert nash_conv(tree, _uniform(tree)) == pytest.approx(4.747222, abs=1e-5)  # OpenSpiel's value
    assert exploitability(tree, _uniform(tree)) == pytest.approx(4.747222 / 2, abs=1e-5)


def test_mcts_beats_a_random_player_at_tic_tac_toe():
    game = _game("tic_tac_toe")
    rng = random.Random(4)
    total = 0.0
    for match in range(4):
        state, seat = game.new_initial_state(), match % 2
        while not state.is_terminal():
            bot = MCTSBot(60, seed=match)
            state.apply_action(bot.step(state) if state.current_player() == seat else state.sample_legal_action(rng))
        total += state.returns()[seat]
    assert total >= 3


def test_is_mcts_decides_the_same_whatever_the_hidden_card():
    game = _game("kuhn_poker")

    def dealt(first, second):
        state = game.new_initial_state()
        state.apply_action(first)
        state.apply_action(second)
        state.apply_action({"tool": "bet", "args": {}})
        return state

    one, other = dealt(0, 0), dealt(2, 1)  # p1 holds 2 in both (its deal indexes the cards left); p0 holds 1 or 3
    assert one.information_state(1) == other.information_state(1)
    moves = {ISMCTSBot(40, seed=7).step(state).text for state in (one, other)}
    assert len(moves) == 1
    drawn = determinize(one, 1, random.Random(1))
    assert drawn.information_state(1) == one.information_state(1)


def test_sampled_legal_actions_are_uniform_over_the_legal_calls():
    state = _game("tic_tac_toe").new_initial_state()
    state.apply_action({"tool": "mark", "args": {"cell": 4}})
    rng = random.Random(0)
    counts = {}
    for _ in range(400):
        action = state.clone().sample_legal_action(rng)
        counts[action.args["cell"]] = counts.get(action.args["cell"], 0) + 1
    assert set(counts) == {0, 1, 2, 3, 5, 6, 7, 8} and min(counts.values()) > 25
