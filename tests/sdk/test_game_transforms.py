"""Game transforms (repeated, misère, zero-sum), starting part-way, simultaneous and turn-based views, PettingZoo
adapters and the game benchmark."""
import random

import pytest

import fg_env
from fg_env.sdk.game import (CHANCE, SIMULTANEOUS, bench_game, misere, pettingzoo_aec, pettingzoo_parallel, repeated,
                             zero_sum_check, zerosum)
from game_contracts import GAMES

PD = GAMES / "prisoners_dilemma.json"


def test_a_repeated_game_is_an_ordinary_contract_whose_returns_are_the_totals():
    contract = repeated(PD, 3)
    assert [issue for issue in fg_env.check(contract) if issue.severity == "error"] == []
    state = fg_env.rl.game(contract).new_initial_state()
    for _ in range(3):
        assert state.current_player() == SIMULTANEOUS
        state.apply_actions({0: {"tool": "choose", "args": {"move": "defect"}},
                             1: {"tool": "choose", "args": {"move": "cooperate"}}})
    assert state.is_terminal() and state.returns() == [15.0, 0.0]
    assert "B chose to cooperate" in state.information_state_string(0)
    assert contract["game"]["max_return"] == 15 and "(repeated 3 times)" in contract["name"]
    with pytest.raises(ValueError, match="one-round"):
        repeated(GAMES / "tic_tac_toe.json", 2)
    with pytest.raises(ValueError, match="end a run early"):
        repeated(GAMES / "kuhn_poker.json", 2)


def test_misere_negates_returns_and_bounds_and_zerosum_centres_them():
    flipped = misere(GAMES / "chicken.json")
    assert flipped["game"]["min_return"] == -1 and flipped["game"]["max_return"] == 10
    state = fg_env.rl.game(flipped).new_initial_state()
    state.apply_actions({0: {"tool": "drive", "args": {"move": "straight"}}, 1: {"tool": "drive", "args": {"move": "swerve"}}})
    assert state.returns() == [-1.0, 1.0]
    centred = zerosum(PD)
    check = zero_sum_check(centred, playouts=8)
    assert check.zero_sum and check.utility == "zero_sum"
    assert zero_sum_check(PD, playouts=20).utility == "general_sum"
    assert zero_sum_check(GAMES / "kuhn_poker.json", playouts=10).zero_sum


def test_a_game_started_part_way_begins_after_the_steps_and_serializes():
    kuhn = fg_env.rl.game(GAMES / "kuhn_poker.json")
    started = kuhn.start_at([{"chance": 2}, {"chance": 0}])
    state = started.new_initial_state()
    assert state.current_player() == 0 and "Your card: 3" in state.observation_string(0)
    state.apply_action({"tool": "bet", "args": {}})
    again = started.deserialize_state(state.serialize())
    assert again.state_key() == state.state_key() and again.history() == state.history()
    with pytest.raises(ValueError):
        kuhn.start_at([{"seat": 0, "tool": "bet", "args": {}}])  # the deal comes first


def test_turn_based_view_hides_the_first_sealed_choice_from_the_second_seat():
    goofspiel = fg_env.rl.game(GAMES / "goofspiel.json").as_turn_based()
    first, second = goofspiel.new_initial_state(), goofspiel.new_initial_state()
    for state in (first, second):
        state.apply_action(0)  # the first prize
        assert state.current_player() == 0
    first.apply_action({"tool": "bid", "args": {"card": 1}})
    second.apply_action({"tool": "bid", "args": {"card": 4}})
    assert first.current_player() == 1 and first.information_state(1) == second.information_state(1)
    assert first.observation(1, "struct") == second.observation(1, "struct")
    joint = fg_env.rl.game(GAMES / "goofspiel.json").new_initial_state()
    joint.apply_action(0)
    joint.apply_actions([{"tool": "bid", "args": {"card": 2}}, {"tool": "bid", "args": {"card": 1}}])
    assert joint.current_player() in (CHANCE, SIMULTANEOUS) and joint.returns() != [0.0, 0.0]


def test_the_aec_adapter_gives_each_agent_its_reward_since_it_last_acted():
    env = pettingzoo_aec(GAMES / "kuhn_poker.json", seed=3)
    env.reset(seed=3)
    rng = random.Random(0)
    seen = {agent: 0.0 for agent in env.possible_agents}
    for agent in env.agent_iter():
        observation, reward, termination, truncation, _ = env.last()
        seen[agent] += reward
        if termination or truncation:
            env.step(None)
            continue
        legal = [index for index, flag in enumerate(observation["action_mask"]) if flag]
        env.step(rng.choice(legal))
    assert env.agents == [] and sum(seen.values()) == 0 and set(seen.values()) != {0.0}


def test_the_parallel_adapter_plays_simultaneous_games_with_joint_actions():
    env = pettingzoo_parallel(GAMES / "rock_paper_scissors.json", seed=1)
    observations, _ = env.reset(seed=1)
    assert set(observations) == {"a", "b"} and sum(observations["a"]["action_mask"]) == 3
    ids = {"a": {"tool": "throw", "args": {"move": "rock"}}, "b": {"tool": "throw", "args": {"move": "scissors"}}}
    _, rewards, terminations, _, _ = env.step(ids)
    assert rewards == {"a": 1.0, "b": -1.0} and all(terminations.values()) and env.agents == []


def test_pettingzoo_api_test_passes_when_pettingzoo_is_installed():
    api_test = pytest.importorskip("pettingzoo.test").api_test
    api_test(pettingzoo_aec(GAMES / "kuhn_poker.json", seed=1), num_cycles=20)


def test_the_game_benchmark_reports_playout_rollout_and_clone_speeds():
    bench = bench_game(GAMES / "nim.json", playouts=2, clones=3)
    assert bench.states > 0 and bench.rollout_states > 0 and bench.clones == 3
    assert bench.states_per_second > 0 and "clone+apply/s" in bench.summary()
