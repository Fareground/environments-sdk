"""Stepped game states — decided on the caller's own thread and cloned by copying the run — against piloted ones
(a run on a thread of its own, cloned by replaying): the same states, legal calls, observations, information
states, returns and results at every decision of seeded random playouts of every example game, and the same for
clones taken at every decision.

A longer run over more playouts: FG_ENV_SLOW=1 pytest tests/test_game_stepping.py
"""
import copy
import os
import random

import pytest
from game_contracts import GAMES, TIC_TAC_TOE, load_game

import fg_env
from fg_env.copying.direct import _ENV_FIELDS, _TURN_FIELDS, _WORLD_FIELDS
from fg_env.copying.stepping import Stepper
from fg_env.game import apply_step, game, random_step
from fg_env.game.runs import ThreadedRun

EXAMPLES = sorted(GAMES.glob("*.json"))
SIMULTANEOUS = ["biased_pennies", "first_price_auction", "goofspiel", "prisoners_dilemma", "rock_paper_scissors"]
PLAYOUTS = 16 if os.environ.get("FG_ENV_SLOW") else 3

PING = {
    "name": "Ping",
    "clock": {"rounds": 2},
    "types": {"player": {"agent": True, "props": {"seat": 0, "pongs": 0}}},
    "entities": {"a": {"type": "player", "props": {"seat": 0}}, "b": {"type": "player", "props": {"seat": 1}}},
    "actions": {
        "ping": {"by": "player", "when": ["$actor.id == 'a'"], "terminal": True,
                 "do": [{"wake": "b", "why": "You were pinged.", "now": True}]},
        "pong": {"by": "player", "when": ["$actor.id == 'b'"], "terminal": True, "do": ["$actor.pongs += 1"]},
    },
    "stages": [{"name": "play", "who": "$it.id == 'a'", "must_act": True}],
    "game": {"players": "player", "seat": "$it.seat", "returns": "$actor.pongs"},
}


def _pair(source, **options):
    stepped = game(source, **options)
    piloted = game(source, **options)
    piloted._stepped = False
    return stepped, piloted


def _signature(state):
    seats = range(state.game.num_players())
    terminal = state.is_terminal()
    out = {"text": str(state), "player": state.current_player(), "history": state.history(), "key": state.state_key(),
           "outcomes": state.chance_outcomes(), "acting": state.acting_players(),
           "observations": [state.observation_string(seat) for seat in seats],
           "structures": [state.observation(seat, "struct") for seat in seats],
           "information": [state.information_state_string(seat) for seat in seats]}
    if state.game.contract.scoring() is not None:
        out["returns"], out["rewards"] = state.returns(), state.rewards()
    if not terminal and not state.is_chance_node():
        out["legal"] = {seat: ([(a.id, a.text) for a in state.legal_tool_calls(seat)], state.unlisted_actions(seat))
                        for seat in state.acting_players()}
    if terminal:
        out["result"] = state.result().to_dict()
    return out


def _step_both(stepped, piloted, rng):
    step = random_step(stepped, rng)
    apply_step(stepped, step)
    apply_step(piloted, step)


def _play_both(stepped_game, piloted_game, seed, playouts=PLAYOUTS, max_steps=200):
    rng = random.Random(seed)
    for _ in range(playouts):
        stepped, piloted = stepped_game.new_initial_state(), piloted_game.new_initial_state()
        try:
            for _ in range(max_steps):
                assert _signature(stepped) == _signature(piloted)
                if stepped.is_terminal():
                    break
                twins = stepped.clone(), piloted.clone()
                try:
                    _step_both(*twins, random.Random(rng.random()))
                    assert _signature(twins[0]) == _signature(twins[1])
                finally:
                    for twin in twins:
                        twin.close()
                _step_both(stepped, piloted, rng)
        finally:
            stepped.close()
            piloted.close()


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_stepped_states_and_their_clones_match_piloted_ones_at_every_decision(path):
    stepped, piloted = _pair(path, seed=5)
    first, other = stepped.new_initial_state(), piloted.new_initial_state()
    assert isinstance(first._run, Stepper) and isinstance(other._run, ThreadedRun)
    first.close()
    other.close()
    _play_both(stepped, piloted, seed=path.stem)
    assert stepped._stepped


@pytest.mark.parametrize("name", SIMULTANEOUS)
def test_sealed_turns_decided_one_seat_at_a_time_match_piloted_ones(name):
    _play_both(*_pair(GAMES / f"{name}.json", seed=2, simultaneous="turn_based"), seed=name)


@pytest.mark.parametrize("name", ["kuhn_poker", "leduc_poker", "goofspiel", "pig"])
def test_sampled_chance_matches_piloted_states(name):
    _play_both(*_pair(GAMES / f"{name}.json", seed=4, chance="sampled"), seed=name)


def test_agents_that_are_not_seats_play_identically_in_stepped_and_piloted_states():
    _play_both(*_pair(TIC_TAC_TOE, players=["x"], others="random", seed=3), seed="others")


def test_a_run_that_records_exposures_is_copied_with_what_every_agent_was_shown():
    contract = copy.deepcopy(TIC_TAC_TOE)
    contract["views"]["board"]["look"] = True
    contract["outputs"]["x_looked"] = {"expr": "$seen(x, 'board')", "type": "bool"}
    stepped, piloted = _pair(contract, seed=1)
    assert stepped._root.world.exposures is not None
    _play_both(stepped, piloted, seed="exposures")


def test_clones_at_a_chance_node_inside_a_call_take_each_outcome_like_piloted_clones():
    stepped, piloted = _pair(GAMES / "leduc_poker.json", seed=6)
    steps = [{"chance": 0}, {"chance": 1}, {"seat": 0, "tool": "call", "args": {}},
             {"seat": 1, "tool": "call", "args": {}}]
    here, there = stepped.new_initial_state(), piloted.new_initial_state()
    for step in steps:
        apply_step(here, step)
        apply_step(there, step)
    assert here.is_chance_node() and _signature(here) == _signature(there)
    for outcome, _ in here.chance_outcomes():
        mine, theirs = here.child(outcome), there.child(outcome)
        assert _signature(mine) == _signature(theirs)
        mine.close()
        theirs.close()
    assert _signature(here) == _signature(there)  # the node itself is untouched by its children


def test_a_turn_that_ends_after_a_chosen_outcome_ends_the_game_like_a_piloted_one():
    stepped, piloted = _pair(GAMES / "pig.json", inputs={"target": 3})
    steps = [{"seat": 0, "tool": "roll", "args": {}}, {"chance": 3}, {"seat": 0, "tool": "stop", "args": {}}]
    here, there = stepped.new_initial_state(), piloted.new_initial_state()
    for step in steps:
        apply_step(here, step)
        apply_step(there, step)
        assert _signature(here) == _signature(there)
    assert here.is_terminal() and here.returns() == [1, -1]


def test_a_seat_woken_to_react_inside_a_call_goes_on_as_a_piloted_run():
    stepped, piloted = _pair(PING, seed=1)
    state, reference = stepped.new_initial_state(), piloted.new_initial_state()
    assert isinstance(state._run, Stepper)
    apply_step(state, {"seat": 0, "tool": "ping", "args": {}})
    apply_step(reference, {"seat": 0, "tool": "ping", "args": {}})
    assert isinstance(state._run, ThreadedRun) and not stepped._stepped
    assert _signature(state) == _signature(reference)
    _play_both(stepped, piloted, seed="ping", playouts=2)


def test_contracts_that_need_a_thread_of_their_own_are_piloted():
    timed = load_game("tic_tac_toe")
    timed["stages"][0]["time_limit"] = 30
    assert not game(timed)._stepped
    assert not game(TIC_TAC_TOE, players=["x"], others=lambda wake: wake.end())._stepped
    assert game(TIC_TAC_TOE)._stepped


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_every_attribute_of_a_stepped_run_is_one_its_copy_accounts_for(path):
    state = game(path, seed=1).new_initial_state()
    while state.is_chance_node():
        state.apply_action(state.chance_outcomes()[0][0])
    run = state._run
    env = run._run()
    turn = state._turn()
    assert set(vars(env)) == _ENV_FIELDS
    assert set(vars(env.world)) == _WORLD_FIELDS
    assert turn is not None and set(vars(turn)) == _TURN_FIELDS
    state.close()


def test_sample_legal_action_draws_the_same_call_from_stepped_and_piloted_states():
    stepped, piloted = _pair(GAMES / "tic_tac_toe.json", seed=2)
    here, there = stepped.new_initial_state(), piloted.new_initial_state()
    rng = random.Random(9)
    while not here.is_terminal():
        seed = rng.random()
        action = here.sample_legal_action(random.Random(seed))
        assert action == there.sample_legal_action(random.Random(seed))
        here.apply_action(action)
        there.apply_action(action)
    assert here.result().to_dict() == there.result().to_dict()


def test_a_stepped_state_that_fails_fails_like_a_piloted_one():
    broken = load_game("nim")
    # Checked at the round's end, a break is the world's and fails the run (an action's own commit would be refused).
    broken["invariants"] = [{"expr": "$world.stones == $inputs.stones", "why": "nobody may take a stone",
                             "check": "round"}]
    stepped, piloted = _pair(broken)
    outcomes = []
    for subject in (stepped, piloted):
        state = subject.new_initial_state()
        state.apply_action(state.legal_actions()[0])
        outcomes.append(state._run.read(lambda env: (env.status, env.error)))
        with pytest.raises(fg_env.RunError):
            state.is_terminal()
    assert outcomes[0] == outcomes[1] and outcomes[0][0] == "failed"
