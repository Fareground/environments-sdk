"""fg_env.author stays inside its budget and its sandbox, and "it works" means the contract really plays."""
import json
from types import SimpleNamespace

import pytest
from test_author import BROKEN, EMPTY, WORKING, FakeOpenAI, call, tool_replies, write

import fg_env


def game(name, **parts):
    """A one-agent contract of three rounds with ``parts`` replacing the defaults."""
    return {"name": name, "clock": {"rounds": 3}, "world": {"x": 0}, "types": {"p": {"agent": True}},
            "entities": {"a": {"type": "p"}}, "actions": {"wait": {"by": "p", "do": ["$world.x += 1"]}},
            "stages": [{"name": "s"}], "outputs": {"x": "$world.x"}, **parts}


#: Checks clean and runs on seeds 1 and 2; its first-round event fails on seed 3.
UNLUCKY = game("Unlucky", world={"table": {"type": "map", "default": {"a": 1}}, "x": 0},
               events=[{"at": 1, "do": [{"if": "$randint(1, 7) == 1", "then": ["$world.x = $world.table['b']"]}]}])
#: Runs to the end, but every choice its one action offers is refused: nothing ever happens.
NOTHING_HAPPENS = game("Nothing happens", actions={"bid": {
    "by": "p", "params": {"n": {"type": "int", "min": 0, "max": 10}},
    "when": [{"expr": "$params.n > 100", "why": "too low"}], "do": ["$world.x += $params.n"]}})
#: Its action divides by zero at the greatest amount it allows, which random play almost never picks.
EDGE_CRASH = game("Edge crash", actions={"set": {
    "by": "p", "params": {"n": {"type": "int", "min": 0, "max": 1000}}, "do": ["$world.x = 10 / ($params.n - 1000)"]}})
WITH_POLICY = {**WORKING, "policies": {"greedy": {"rules": [{"do": "take", "with": {"count": 1}}]}}}


@pytest.mark.parametrize("who", ["anthropic:claude-opus-5", "openai:gpt-5", "cfr:/etc/passwd", "mcts:100000000",
                                 "minimax", "greedy"])
def test_the_run_tool_plays_only_the_contracts_own_agents(monkeypatch, who):
    made = []
    monkeypatch.setattr("fg_env.participants.llm.official_client", lambda *a: made.append(a))
    monkeypatch.setattr("fg_env.game.algorithms.participants.algorithm_participant", lambda *a: made.append(a))
    client = FakeOpenAI([write(WITH_POLICY)], [call("run", participants={"*": who})], [])

    fg_env.author("A game.", "openai:m", client=client)

    assert tool_replies(client)[1] == (f"Bad tool call: run: participants.*: {who!r} cannot play here: use 'random', "
                                       "'idle', 'policy:greedy'. Nothing was run.")
    assert made == []


def test_the_run_tool_plays_random_idle_and_the_contracts_policies():
    client = FakeOpenAI([write(WITH_POLICY)], [call("run", participants={"north": "policy:greedy", "south": "idle"}),
                                               call("run", participants={"*": "random"}), call("run", participants=1)],
                        [])

    fg_env.author("A game.", "openai:m", client=client)

    by_policy, by_random, wrong = tool_replies(client)[1:]
    assert "ended by last_stone" in by_policy and "north: taken=15" in by_policy
    assert by_random.startswith(("completed", "ended"))
    assert wrong.startswith("Bad tool call: run: participants maps a type or entity id to one of 'random'")


@pytest.mark.parametrize("contract, problem", [
    (UNLUCKY, "a run with random agents (seed 3) failed in round 1: events[0].do[0].then[0]: no field 'b'"),
    (NOTHING_HAPPENS, "a run with random agents (seed 1) shows agents_never_acted: participants: a took no action"),
    (EDGE_CRASH, "a run with agents choosing edge values (seed 1) shows action_rule_failed: actions.set.do[0]"),
])
def test_it_works_only_when_every_test_run_plays(contract, problem):
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]  # each checks clean

    result = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(contract)], [], [], []))

    assert not result.ok and result.problem.startswith(problem), result.problem


def test_a_contract_longer_than_the_test_budget_works_with_its_untested_rounds_named(tmp_path, monkeypatch):
    monkeypatch.setattr("fg_env.authoring.testing.TEST_SECONDS", 0.2)
    long = game("Long", clock={"rounds": 100_000})
    client = FakeOpenAI([write(long)], [])

    result = fg_env.author("A game.", "openai:m", client=client, out=str(tmp_path / "long.json"))

    assert result.ok and result.stop == "done"
    note = tool_replies(client)[0]
    assert note.startswith("Saved revision 1: it works — it checks") and "runs without a problem on 3 seeds" in note
    assert " of 100,000 rounds in every test run within the 0.2s test budget; longer runs untested;" in note
    assert result.tested.untested in note and f"PARTLY TESTED: {result.tested.untested}" in result.summary()


def test_a_crash_within_the_rounds_the_test_budget_reaches_still_counts(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.testing.TEST_SECONDS", 5)
    late = game("Late", clock={"rounds": 100_000}, world={"table": {"type": "map", "default": {"a": 1}}, "x": 0},
                events=[{"when": "$round - 20 == 0", "do": ["$world.x = $world.table['b']"]}])

    result = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(late)], [], [], []))

    assert not result.ok and result.problem.startswith("a run with random agents (seed 1) failed in round 20")


def test_stopping_on_a_broken_revision_after_a_working_one_sends_the_model_back_once():
    client = FakeOpenAI([write(WORKING)], [write(BROKEN)], [], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    back = client.sent[3][-1]["content"]
    assert back.startswith("Revision 2 does not work: bogus: 'bogus' is not a field here")
    assert back.endswith("Stopping now keeps revision 1; or fix it and save it again.")
    assert (result.ok, result.stop, result.contract, result.usage["calls"]) == (True, "done", WORKING, 4)


def test_a_working_revision_is_written_to_out_at_once(tmp_path):
    out = tmp_path / "env.json"
    client = FakeOpenAI([write(WORKING)], KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        fg_env.author("A game.", "openai:m", client=client, out=str(out))

    assert json.loads(out.read_text()) == WORKING and [p.name for p in tmp_path.iterdir()] == ["env.json"]


def test_an_out_in_a_missing_folder_is_refused_before_any_model_call(tmp_path):
    client = FakeOpenAI([write(WORKING)], [])

    with pytest.raises(ValueError, match="the folder .* does not exist"):
        fg_env.author("A game.", "openai:m", client=client, out=str(tmp_path / "missing" / "env.json"))
    assert client.sent == []


def test_the_instruction_maps_the_guides_commands_to_the_tools():
    client = FakeOpenAI([write(WORKING)], [])

    fg_env.author("A game.", "openai:m", client=client)

    assert "`fg-env check` is check, `fg-env preview <file> <id>` is preview(agent)" in client.sent[0][1]["content"]


def test_the_summary_shows_what_the_model_said_last():
    assert "the model's last words: Done." in fg_env.author(
        "A game.", "openai:m", client=FakeOpenAI([write(WORKING)], [])).summary()


def test_cache_reads_are_counted_apart_and_weigh_a_tenth_in_the_budget():
    client = FakeOpenAI([write(WORKING)], [call("check")], [call("check")], [], tokens=1000, cached=900)

    result = fg_env.author("A game.", "openai:m", client=client, budget={"tokens": 400})

    # Each call spends 100 + 10 fresh tokens and 900 cached ones, weighing 90: the budget ends after two, not one.
    assert (result.stop, result.usage["calls"]) == ("tokens", 2)
    assert (result.usage["input_tokens"], result.usage["cached_tokens"]) == (200, 1800)
    assert "220 tokens (+1,800 cached)" in result.summary()


def test_anthropic_cache_reads_are_counted_apart():
    def create(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Done.")], usage=SimpleNamespace(
            input_tokens=5, output_tokens=5, cache_read_input_tokens=800, cache_creation_input_tokens=50))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)),
                           budget={"calls": 1})

    usage = result.usage
    assert (usage["input_tokens"], usage["cached_tokens"], usage["cache_write_tokens"]) == (5, 800, 50)


def test_an_empty_reply_is_retried_and_one_that_persists_stops_the_session(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.author.time.sleep", lambda seconds: None)

    recovered = fg_env.author("A game.", "openai:m", client=FakeOpenAI(EMPTY, [write(WORKING)], []))
    lost = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(WORKING)], *[EMPTY] * 5))

    assert recovered.ok and recovered.stop == "done"
    assert lost.ok and lost.stop == "error: EmptyReply: the provider sent a response with no reply in it"


@pytest.mark.parametrize("broken", ["check", "load"])
def test_whatever_a_models_contract_raises_is_its_problem_not_a_crash(monkeypatch, tmp_path, broken):
    # What the test process does with a saved contract, run here so the engine can be broken on purpose.
    from fg_env.authoring import testing, workbench
    from fg_env.authoring.testing import _test
    from fg_env.authoring.workbench import _tool

    def boom(*args, **kwargs):
        raise TypeError("'int' object is not iterable")

    for module in (testing, workbench):
        if hasattr(module, broken):
            monkeypatch.setattr(module, broken, boom)
    path = tmp_path / "env.json"
    path.write_text(json.dumps(WORKING))

    assert _test(str(path), 5, [1], 1)["problem"] == "TypeError: 'int' object is not iterable"
    tool = "check" if broken == "check" else "preview"
    assert _tool(tool, str(path), {} if tool == "check" else {"agent": "north"}) == \
        "TypeError: 'int' object is not iterable"


def hog(megabytes):
    """Hold ``megabytes`` of memory for a moment (run in the test process by the sandbox test below)."""
    import time

    held = b"x" * (megabytes * 1024 * 1024)
    time.sleep(1)
    return len(held)


def test_the_test_process_stops_itself_past_its_memory_ceiling_and_the_next_call_starts_afresh():
    """A contract too big to test must not push the machine into swap (audit 9 author M7)."""
    from fg_env.authoring.sandbox import Sandbox, TooBig

    with Sandbox(memory_mb=200) as box:
        with pytest.raises(TooBig, match="more than 200 MB"):
            box.call("test_author_safety:hog", {"megabytes": 400}, seconds=30)
        assert box.call("test_author_safety:hog", {"megabytes": 10}, seconds=30) == 10 * 1024 * 1024
