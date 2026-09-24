"""fg_env.author says "it works" only when it does: what its test runs judge, what they are shown, how long they may
take, and what a later revision gave up."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import fg_env
from fg_env.authoring.sandbox import Sandbox, TooSlow
from test_author import WORKING, FakeOpenAI, call, edit, tool_replies, write
from test_author_safety import game

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"
LEMONADE = json.loads((EXAMPLES / "lemonade_stand.json").read_text())


def lemonade(**parts):
    return {**json.loads(json.dumps(LEMONADE)), **parts}


def authored(contract, **kwargs):
    """The author session that saves ``contract`` and stops; its tool replies."""
    client = FakeOpenAI([write(contract)], [], [], [])
    return fg_env.author("A game.", "openai:m", client=client, **kwargs), tool_replies(client)


def test_a_view_that_breaks_on_a_state_play_reaches_is_not_working():
    # At the greatest price the tool allows the view divides by zero: an agent reading its update after that breaks.
    views = {**LEMONADE["views"], "margin": {"for": "seller", "title": "Margin", "show": "{$round(10 / ($actor.price - 5))}"}}

    result, _ = authored(lemonade(views=views))

    assert not result.ok and "views.margin" in result.problem and "division by zero" in result.problem


def test_an_output_that_fails_only_when_agents_do_not_act_is_not_working():
    outputs = {**LEMONADE["outputs"], "spread": {"expr": "1 / ($avg(seller, $it.price) - 1.0)", "type": "number"}}

    result, _ = authored(lemonade(outputs=outputs))

    assert not result.ok
    assert result.problem.startswith("a run with idle agents (seed 1) has an output that fails: outputs.spread")


def test_a_contract_whose_agents_can_do_nothing_or_that_measures_nothing_is_not_working():
    no_outputs = {k: v for k, v in WORKING.items() if k != "outputs"}
    client = FakeOpenAI([write(WORKING)], [edit(("actions", ...))], [write(no_outputs)], [], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    gutted, unmeasured = tool_replies(client)[1:]
    assert gutted.startswith("Saved revision 2, but it does not work yet: types.player: this agent type has no action")
    assert unmeasured.startswith("Saved revision 3, but it does not work yet: outputs: it declares no outputs")
    assert result.ok and result.contract == WORKING


def test_check_warnings_reach_the_model_and_only_a_clean_check_is_called_clean():
    warned = game("Noted", world={"x": 0, "note": ""},
                  actions={"wait": {"by": "p", "do": ["$world.x += 1", "$world.note = '{$world.x}'"]}})

    result, replies = authored(warned)

    assert result.ok and "it checks with no errors (warnings below)" in replies[0] and "checks clean" not in replies[0]
    assert "Check warnings:\n[warning] actions.wait.do[1]: stores `{$...}` literally" in replies[0]


@pytest.mark.parametrize("example, host", [("debate_judged", "judge"), ("tavern_gm", "game_master")])
def test_a_contract_that_consults_a_host_is_tested_with_the_stand_in_stubs(example, host):
    result, replies = authored(json.loads((EXAMPLES / "host" / f"{example}.json").read_text()))

    assert result.ok, result.problem
    assert f"Its host calls ({host}) were answered by the SDK's stand-in stubs, not a model" in replies[0]


def test_a_contract_too_slow_to_test_is_reported_and_never_hangs_the_session(monkeypatch):
    # Every one of a thousand sellers reads a view listing every seller: check alone takes many seconds.
    monkeypatch.setattr("fg_env.authoring.testing.TEST_SECONDS", 0.5)
    monkeypatch.setattr("fg_env.authoring.workbench.RUN_SECONDS", 0.5)
    monkeypatch.setattr("fg_env.authoring.sandbox.GRACE_SECONDS", 1)
    crowded = lemonade(entities={}, population=[{"type": "seller", "count": 1000}])
    client = FakeOpenAI([write(crowded)], [call("check")], [])

    result = fg_env.author("A town.", "openai:m", client=client)

    assert not result.ok and result.problem.startswith(
        "too slow to test: checking it was still going when the 0.5s test budget ran out → make each round cheaper")
    assert tool_replies(client)[1].startswith("Too slow: check was still going after 0.5s")


def test_write_contract_takes_the_json_object_itself():
    client = FakeOpenAI([call("write_contract", contract=WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.ok and result.contract == WORKING


def test_random_agents_play_more_seeds_while_test_time_lasts():
    # Its end-of-round event fails on seeds 8 and 12 only: the first three seeds, and check's plays, all pass.
    rare = game("Rare", world={"table": {"type": "map", "default": {"a": 1}}, "x": 0},
                events=[{"phase": "end", "do": [{"if": "$random() * 20 < 1", "then": ["$world.x = $world.table['b']"]}]}])

    result, _ = authored(rare)

    assert not result.ok and result.problem.startswith("a run with random agents (seed 8) failed in round")
    _, replies = authored(WORKING)
    assert "runs to the end on 20 seeds with random agents, 3 with idle ones" in replies[0]


def test_a_rule_that_fails_for_some_of_the_choices_random_agents_make_is_not_working():
    # It divides by zero when n % 5 == 3: never on the edges 0 and 100, often in random play.
    faulty = game("Faulty", actions={"set": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": 100}},
                                             "do": ["$world.x = 10 / ($params.n % 5 - 3)"]}})

    result, _ = authored(faulty)

    assert not result.ok and "shows action_rule_failed: actions.set.do[0]" in result.problem
    assert result.problem.startswith("a run with random agents")


def test_a_guide_part_longer_than_one_reply_says_where_it_was_cut_and_reads_on():
    whole = fg_env.guide("patterns")
    client = FakeOpenAI([call("guide", part="patterns"), call("guide", part="patterns", start=12000)], [])

    result = fg_env.author("A game.", "openai:m", client=client, budget={"calls": 1})

    first, second = [m["content"] for m in result.messages if m["role"] == "tool"]
    assert first == whole[:12000] + (f"\n[cut at 12,000 of {len(whole):,} characters: read on with "
                                     "guide('patterns', start=12000)]")
    assert second.startswith(whole[12000:24000]) and "start=24000" in second


def test_a_refusal_after_a_working_revision_is_not_done():
    replies = iter([[SimpleNamespace(type="tool_use", id="t1", name="write_contract",
                                     input={"contract": json.dumps(WORKING)})], []])

    def create(**kwargs):
        blocks = next(replies)
        return SimpleNamespace(content=blocks, stop_reason="tool_use" if blocks else "refusal",
                               usage=SimpleNamespace(input_tokens=5, output_tokens=5))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)))

    assert (result.ok, result.stop, result.contract) == (True, "refused", WORKING)


def test_the_revision_limit_names_the_revision_kept_and_ends_the_session(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.workbench.MAX_REVISIONS", 2)
    client = FakeOpenAI([write(WORKING)], [write({**WORKING, "bogus": 1}), write(WORKING)], [call("check")])

    result = fg_env.author("A game.", "openai:m", client=client)

    replies = [m["content"] for m in result.messages if m["role"] == "tool"]
    assert replies[2] == "Revision limit reached (2): nothing more is saved; revision 1, the best that works, is kept."
    assert (result.ok, result.stop, result.usage["calls"], result.contract) == (True, "revisions", 2, WORKING)


def test_the_sandbox_bounds_time_and_survives_what_its_child_does(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.sandbox.GRACE_SECONDS", 0.5)
    with Sandbox() as box:
        assert box.call("json:dumps", {"obj": [1]}, 1) == "[1]"  # a new child's start is not counted
        assert box.call("json:dumps", {"obj": [1]}, 5) == "[1]"
        with pytest.raises(RuntimeError, match="JSONDecodeError"):
            box.call("json:loads", {"s": "{"}, 5)
        with pytest.raises(TooSlow):
            box.call("subprocess:call", {"args": ["sleep", "30"]}, 0.1)
        assert box.call("json:dumps", {"obj": 2}, 5) == "2"  # a new child after the slow one was killed
        with pytest.raises(RuntimeError, match="died"):
            box.call("os:_exit", {"status": 3}, 5)


def test_a_feed_with_a_fallback_is_tested_with_its_fallback_not_the_stub():
    # The stub feed answers numbers up to 100, which this world refuses; the contract's own stand-in is what it plays.
    weather = game("Weather", world={"x": 0, "temp": {"type": "number", "default": 10, "max": 45}},
                   feeds={"weather": {"host": "weather", "into": "world.temp", "fallback": 20}})

    result, replies = authored(weather)

    assert result.ok, result.problem
    assert "stand-in stubs" not in replies[0]
