"""`end[].check: action` ends a run the moment its condition holds, in every kind of stage."""
import json

import fg_env

GUESS = {
    "name": "Guess",
    "clock": {"rounds": 5},
    "world": {"secret": 7, "found_by": ""},
    "types": {"player": {"agent": True, "props": {"guesses": 0}}},
    "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}, "cy": {"type": "player"}},
    "actions": {"guess": {"by": "player", "params": {"number": {"type": "int", "min": 1, "max": 9}},
                          "do": ["$actor.guesses += 1",
                                 {"if": "$params.number == $world.secret", "then": ["$world.found_by = $actor.id"]}]}},
    "end": [{"when": "$world.found_by != ''", "winner": "$entity($world.found_by)", "check": "action",
             "say": "{$entity($world.found_by).name} found it."}],
    "outputs": {"guesses": "$sum(player, $it.guesses)"},
}


def _guesser(numbers):
    def play(wake):
        wake.call("guess", {"number": numbers[wake.entity_id]})
        wake.end()
    return play


def _contract(**changes):
    contract = json.loads(json.dumps(GUESS))
    contract.update(changes)
    return contract


def test_an_action_end_stops_a_sequential_stage_before_the_next_agent_moves():
    result = fg_env.run(GUESS, _guesser({"ann": 3, "bob": 7, "cy": 7}), seed=1)
    assert result.status == "ended" and result.rounds == 1
    assert result.winner == "bob" and result.outputs["guesses"] == 2
    assert result.events[-1]["text"] == "bob found it."


def test_an_end_without_check_still_waits_for_the_stage_to_finish():
    contract = _contract()
    del contract["end"][0]["check"]
    result = fg_env.run(contract, _guesser({"ann": 3, "bob": 7, "cy": 7}), seed=1)
    assert result.status == "ended" and result.outputs["guesses"] == 3


def test_an_action_end_stops_committing_sealed_choices_in_turn_order():
    contract = _contract(stages=[{"name": "guess", "turns": "simultaneous"}])
    result = fg_env.run(contract, _guesser({"ann": 7, "bob": 7, "cy": 2}), seed=1)
    assert result.status == "ended" and result.winner == "ann" and result.outputs["guesses"] == 1


def test_an_action_end_stops_a_continuous_clock_at_that_moment():
    contract = _contract(clock={"mode": "continuous", "horizon": 30},
                         stages=[{"name": "guess", "turns": "scheduled", "interval": 10}],
                         world={"secret": 7, "found_by": "", "tries": 0})
    contract["actions"]["guess"]["do"] = ["$actor.guesses += 1", "$world.tries += 1",
                                          {"if": "$world.tries == 4", "then": ["$world.found_by = $actor.id"]}]
    result = fg_env.run(contract, _guesser({"ann": 1, "bob": 1, "cy": 1}), seed=1)
    assert result.status == "ended" and result.winner == "ann" and result.time == 10
    assert result.outputs["guesses"] == 4


def test_an_action_end_fires_after_an_effect_block_outside_any_turn():
    events = [{"phase": "end", "do": ["$world.found_by = 'cy'"]}, {"phase": "end", "do": ["$world.found_by = ''"]}]
    result = fg_env.run(_contract(events=events), _guesser({"ann": 1, "bob": 1, "cy": 1}), seed=1)
    assert result.status == "ended" and result.winner == "cy" and result.rounds == 1
    at_round_end = _contract(events=events)
    del at_round_end["end"][0]["check"]
    assert fg_env.run(at_round_end, _guesser({"ann": 1, "bob": 1, "cy": 1}), seed=1).status == "completed"


def test_runs_with_action_ends_resume_exactly_from_snapshots_and_stops():
    numbers = {"ann": 1, "bob": 2, "cy": 3}
    contract = _contract(events=[{"at": 3, "phase": "start", "do": ["$world.secret = 2"]}])
    straight = fg_env.load(contract, seed=5).run(_guesser(numbers)).to_dict()
    assert straight["status"] == "ended" and straight["rounds"] == 3

    env = fg_env.load(contract, seed=5)
    env.run(_guesser(numbers), rounds=2)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run(_guesser(numbers)).to_dict() == straight

    points = {"n": 0}

    def stop(_env):
        points["n"] += 1
        return points["n"] == 9

    env = fg_env.load(contract, seed=5)
    env.run(_guesser(numbers), stop=stop)
    assert env.status == "stopped"
    assert env.run(_guesser(numbers)).to_dict() == straight


def test_an_unknown_end_check_is_reported_with_the_choices():
    issues = fg_env.check(_contract(end=[{"when": "$round > 2", "check": "actions"}]), rounds=0)
    assert [str(i) for i in issues] == ["end[0].check: unknown check 'actions' → did you mean 'action'?"]
