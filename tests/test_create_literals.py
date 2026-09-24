"""Entity creation never evaluates participant text or runtime values as expressions."""
import json

from family_fixtures import Nothing, scratch_family

import fg_env
from fg_env.expr import Untrusted
from fg_env.registry import family_action, mode


def test_participant_text_stored_by_a_native_op_is_never_evaluated():
    with scratch_family("test_notes"):
        mode("test_notes", "pad", Nothing, "A notepad.")(lambda name, config, contract: {})

        @family_action("test_notes", ("pad",), "make", keys=("text", "plain"),
                       example='{"test_notes": "n", "action": "make", "text": "$params.text"}')
        def _make(runner, effect, vars, where):
            world = runner.world
            text = runner.eval(effect["text"], vars)
            evaluation = world.evaluation
            evaluation.create("note", None, None, {"text": text}, None, evaluation.scope(**vars), where)
            evaluation.create("note", None, None, {"text": effect["plain"]}, None, evaluation.scope(**vars), where,
                              evaluate=False)

        contract = {"name": "Notes", "clock": {"rounds": 1},
                    "world": {"secret": {"type": "text", "default": "the vault code is 4321"}},
                    "types": {"writer": {"agent": True}, "note": {"props": {"text": {"type": "text", "default": ""}}}},
                    "entities": {"w": {"type": "writer"}},
                    "mechanisms": {"n": {"kind": "test_notes", "mode": "pad"}},
                    "actions": {"jot": {"by": "writer", "params": {"text": "text"},
                                        "do": [{"test_notes": "n", "action": "make", "text": "$params.text",
                                                "plain": "$world.secret"}],
                                        "terminal": True}},
                    "stages": [{"name": "s", "turns": "sequential"}]}
        env = fg_env.load(contract, seed=1)

        def play(wake):
            assert wake.call("jot", {"text": "$world.secret"}).ok
            wake.end()

        result = env.run(play)
        assert result.status == "completed", result.error
        texts = [e["props"]["text"] for e in env.entities("note")]
        assert texts == ["$world.secret", "$world.secret"]
        assert isinstance(env.world.entities["note_1"].properties["text"], Untrusted)
        # The end state shows the world's own secret prop; nothing else may hold its value.
        assert "4321" not in json.dumps({**result.to_dict(), "state": result.state["types"]}, default=str)
