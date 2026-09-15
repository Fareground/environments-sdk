"""Found by the capability re-verification: async participants silently did nothing; map link values passed check."""
import pytest

import fg_env

BASE = {"name": "Bugs", "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "props": {"cash": 1}}},
        "entities": {"ann": {"type": "player"}, "bo": {"type": "player"}},
        "actions": {"pay": {"by": "player", "do": ["$actor.cash -= 1"], "terminal": True}},
        "stages": [{"name": "play", "turns": "sequential"}]}


def test_an_async_participant_is_refused_instead_of_silently_never_acting():
    async def agent(wake):
        wake.call("pay")

    with pytest.raises(TypeError, match="async"):
        fg_env.load(BASE, seed=1).run(agent)

    class Agent:
        async def __call__(self, wake):
            wake.call("pay")

    with pytest.raises(TypeError, match="async"):
        fg_env.load(BASE, seed=1).run({"player": Agent()})

    def sneaky(wake):  # a plain function that hands back a coroutine
        return agent(wake)

    result = fg_env.load(BASE, seed=1).run(sneaky)
    assert result.status == "failed" and "async" in result.error


def test_a_non_number_link_value_is_a_check_error_not_a_run_failure():
    contract = {**BASE, "relations": {"owes": {}},
                "links": [{"relation": "owes", "from": "ann", "to": "bo", "value": {"amount": 5, "since": 1}}]}
    errors = [str(i) for i in fg_env.check(contract) if i.severity == "error"]
    assert any("links[0].value" in e and "must be a number" in e for e in errors)
