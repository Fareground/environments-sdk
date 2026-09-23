"""Caller inputs still win over an arm's inputs, but a replaced arm input is reported by name with both values."""
import fg_env

BREAKER = {
    "name": "Breaker",
    "clock": {"rounds": 2},
    "inputs": {"breaker": {"type": "bool", "default": False}, "fee": {"type": "number", "default": 0.1}},
    "types": {"trader": {"agent": True, "props": {"trades": 0}}},
    "entities": {"t1": {"type": "trader"}},
    "actions": {"trade": {"by": "trader", "do": "$actor.trades += 0 if $inputs.breaker else 1"}},
    "arms": {"control": {}, "halt": {"inputs": {"breaker": True}}},
    "outputs": {"breaker": "$inputs.breaker", "trades": "$sum(trader, $it.trades)"},
}


def _overrides(result):
    return [(d["path"], d["message"]) for d in result.diagnostics if d["code"] == "arm_input_overridden"]


def test_a_caller_input_that_replaces_an_arm_input_still_wins_and_is_reported():
    result = fg_env.run(BREAKER, arm="halt", inputs={"breaker": False, "fee": 0.2}, seed=1)
    assert result.outputs["breaker"] is False
    assert _overrides(result) == [("arms.halt.inputs.breaker", "the caller's input breaker=false replaced arm 'halt''s "
                                                               "breaker=true, so these runs do not test what the arm sets")]
    assert "diagnostic: arms.halt.inputs.breaker" in result.summary()


def test_caller_inputs_the_arm_does_not_set_or_that_agree_with_it_are_not_reported():
    assert _overrides(fg_env.run(BREAKER, arm="halt", inputs={"breaker": True, "fee": 0.2}, seed=1)) == []
    assert _overrides(fg_env.run(BREAKER, arm="control", inputs={"breaker": True}, seed=1)) == []
    assert _overrides(fg_env.run(BREAKER, arm="halt", seed=1)) == []


def test_an_experiment_table_warns_about_the_arm_its_inputs_cancel():
    table = fg_env.experiment(BREAKER, runs=2, inputs={"breaker": False}).table()
    assert "warning: the caller's input breaker=false replaced arm 'halt''s breaker=true" in table
    assert "arm 'control'" not in table
