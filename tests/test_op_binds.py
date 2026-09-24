"""Native effect ops that bind locals for later effects and templates."""
from family_fixtures import Nothing, scratch_family

import fg_env
from fg_env.registry import family_action, mode


def test_a_native_op_can_bind_a_local_that_later_effects_and_outcomes_read():
    with scratch_family("test_picker"):
        mode("test_picker", "numbers", Nothing, "Picks numbers.")(lambda name, config, contract: {})

        @family_action("test_picker", ("numbers",), "pick", binds=("as",),
                       example='{"test_picker": "x", "action": "pick", "as": "picked"}')
        def _pick(runner, effect, vars, where):
            vars[effect["as"]] = 42

        def pick(bound):
            return {"test_picker": "x", "action": "pick", "as": bound}

        contract = {"name": "Binds", "clock": {"rounds": 1}, "world": {"n": 0},
                    "types": {"p": {"agent": True}}, "entities": {"p": {"type": "p"}},
                    "mechanisms": {"x": {"kind": "test_picker", "mode": "numbers"}},
                    "actions": {"go": {"by": "p", "do": [pick("picked"), "$world.n = $picked"],
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
        bad = {**contract, "actions": {"go": {"by": "p", "do": [pick("not a name")], "terminal": True}}}
        assert any("names a local" in i.message for i in fg_env.check(bad) if i.severity == "error")
        reserved = {**contract, "actions": {"go": {"by": "p", "do": [pick("actor")], "terminal": True}}}
        assert any("built-in root" in i.message for i in fg_env.check(reserved) if i.severity == "error")
