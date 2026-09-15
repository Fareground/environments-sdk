"""Native effect ops that bind locals for later effects and templates."""
import fg_env
from fg_env.sdk.registry import OPS, effect_op


def test_a_native_op_can_bind_a_local_that_later_effects_and_outcomes_read():
    name = "test_pick_number"

    @effect_op(name, keys=(), literal=(name,), binds=("as",), example='{"test_pick_number": "x", "as": "picked"}')
    def _pick(runner, effect, vars, where):
        vars[effect["as"]] = 42

    try:
        contract = {"name": "Binds", "clock": {"rounds": 1}, "world": {"n": 0},
                    "types": {"p": {"agent": True}}, "entities": {"p": {"type": "p"}},
                    "actions": {"go": {"by": "p", "do": [{name: "x", "as": "picked"}, "$world.n = $picked"],
                                       "outcome": "Picked {$picked}.", "terminal": True}},
                    "stages": [{"name": "s", "turns": "sequential"}]}
        assert not [i for i in fg_env.check(contract) if i.severity == "error"]
        env = fg_env.load(contract, seed=1)
        told = []

        def play(wake):
            told.append(wake.call("go").text)
            wake.end()

        env.run(play)
        assert env.props["n"] == 42 and told == ["Picked 42."]
        bad = {**contract, "actions": {"go": {"by": "p", "do": [{name: "x", "as": "not a name"}], "terminal": True}}}
        assert any("names a local" in i.message for i in fg_env.check(bad) if i.severity == "error")
        reserved = {**contract, "actions": {"go": {"by": "p", "do": [{name: "x", "as": "actor"}], "terminal": True}}}
        assert any("built-in root" in i.message for i in fg_env.check(reserved) if i.severity == "error")
    finally:
        OPS.pop(name, None)
