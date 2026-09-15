"""Entity creation never evaluates participant text or runtime values as expressions."""
import fg_env
from fg_env.sdk.expr import Untrusted
from fg_env.sdk.registry import OPS, effect_op


def test_participant_text_stored_by_a_native_op_is_never_evaluated():
    name = "test_make_note"

    @effect_op(name, keys=("text", "plain"), literal=(name,), example='{"test_make_note": "n", "text": "$params.text"}')
    def _make(runner, effect, vars, where):
        world = runner.world
        text = runner.eval(effect["text"], vars)
        world.create("note", None, None, {"text": text}, None, world.scope(**vars), where)
        world.create("note", None, None, {"text": effect["plain"]}, None, world.scope(**vars), where, evaluate=False)

    try:
        contract = {"name": "Notes", "clock": {"rounds": 1},
                    "world": {"secret": {"type": "text", "default": "the vault code is 4321"}},
                    "types": {"writer": {"agent": True}, "note": {"props": {"text": {"type": "text", "default": ""}}}},
                    "entities": {"w": {"type": "writer"}},
                    "actions": {"jot": {"by": "writer", "params": {"text": "text"},
                                        "do": [{name: "n", "text": "$params.text", "plain": "$world.secret"}],
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
        assert "4321" not in json_dump(result)
    finally:
        OPS.pop(name, None)


def json_dump(result):
    import json

    return json.dumps(result.to_dict(), default=str)
