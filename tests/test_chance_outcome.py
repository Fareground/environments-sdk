"""An action's `outcome` describes it succeeding: when its `chance` roll fails, the actor is told it did not succeed,
never the success text."""
import fg_env

STEP = {
    "name": "Step",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"pos": 0}}},
    "entities": {"ann": {"type": "p"}},
    "actions": {"move": {"by": "p", "params": {"n": {"type": "int", "min": 1, "max": 3}}, "chance": 0.0,
                         "do": ["$actor.pos += $params.n"], "outcome": "You moved {$params.n}."}},
}


def _told(contract):
    told = []
    fg_env.run(contract, lambda wake: told.append(wake.call("move", {"n": 2})), seed=1)
    return told[0]


def test_a_failed_roll_tells_the_actor_it_did_not_succeed():
    result = _told(STEP)
    assert result.ok and not result.data["success"]
    assert result.text == "Move did not succeed."


def test_a_successful_roll_tells_the_outcome():
    sure = {**STEP, "actions": {"move": {**STEP["actions"]["move"], "chance": 1.0}}}
    assert _told(sure).text == "You moved 2."
