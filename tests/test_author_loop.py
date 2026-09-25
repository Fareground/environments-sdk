"""fg_env.author's loop: every view is read however few reads a turn allows, a gutted or flat contract is named, the
model is told its limits and pays one revision per change, deterministic contracts test fast, and the verdict says how
much agents read and whether their brief gives them a goal."""
import json
from types import SimpleNamespace

from test_author import WORKING, FakeOpenAI, ProviderError, call, edit, tool_replies, write
from test_author_best import TAVERN
from test_author_honest import LEMONADE, authored, lemonade

import fg_env
from fg_env.__main__ import main

BROKEN_VIEW = {"for": "seller", "look": True, "show": "{1 / ($round - $round)}"}
#: The lemonade stand with a goal in its sellers' brief.
AIMED = lemonade(brief={**LEMONADE["brief"], "roles": {"seller": "Your goal: earn the most by the end."}})


def test_a_view_behind_more_views_than_a_turn_has_free_reads_is_read_and_its_failure_found():
    many = {**LEMONADE["views"], **{f"v{n}": {"for": "seller", "look": True, "show": f"view {n}"} for n in range(8)},
            "zz": BROKEN_VIEW}
    one_read = lemonade(views={**LEMONADE["views"], "a": {"for": "seller", "look": True, "show": "a"},
                               "zz": BROKEN_VIEW},
                        stages=[{**LEMONADE["stages"][0], "max_calls": 1}])

    for contract in (lemonade(views=many), one_read):
        result, _ = authored(contract)

        assert not result.ok and "views.zz" in result.problem and "division by zero" in result.problem


def test_a_rule_rewritten_to_do_nothing_is_named_and_kept_only_once_confirmed():
    event = LEMONADE["events"][0]
    gutted = lemonade(events=[{**event, "do": [{**event["do"][0], "do": ["$it.earned += 0"]}]}])
    client = FakeOpenAI([write(LEMONADE)], [write(gutted)], [], [write(gutted)], [])

    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)

    replies = tool_replies(client)
    assert "But it removed events.0 (its do now does nothing), which revision 1 has" in replies[1]
    assert replies[2] == "Revision 2 saved again unchanged: its removals are confirmed, and it is kept."
    assert result.ok and result.kept == 2
    summary = result.summary()
    assert "changed since revision 1, the first that worked: events ~0" in summary
    assert "REMOVED since revision 1, the first that worked: events.0 (its do now does nothing)" in summary


def test_a_rule_whose_effects_fired_before_and_never_fire_now_does_nothing_however_it_was_rewritten():
    event = LEMONADE["events"][0]
    never = lemonade(events=[{**event, "when": "$round < 0"}])  # the payout event is still there, and never runs
    client = FakeOpenAI([write(LEMONADE)], [write(never)], [])

    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)

    assert "But it removed events.0 (its effects changed nothing in any test run), which revision 1 has" in \
        tool_replies(client)[1]
    assert result.contract == LEMONADE and result.kept == 1

    itself = lemonade(events=[{**event, "do": [{**event["do"][0], "do": ["$it.earned = $it.earned"]}]}])
    client = FakeOpenAI([write(LEMONADE)], [write(itself)], [])
    assert fg_env.author("A lemonade stand duel.", "openai:m", client=client).kept == 1
    assert "events.0 (its do now does nothing)" in tool_replies(client)[1]


def test_a_rule_gutted_by_an_arithmetic_identity_changes_nothing_and_counts_as_removed():
    """`$x = $x + 0` still runs; what counts is that it never changed anything in the test runs."""
    event = LEMONADE["events"][0]
    identity = lemonade(events=[{**event, "do": [{**event["do"][0], "do": ["$it.earned = $it.earned * 1 + 0"]}]}])
    client = FakeOpenAI([write(LEMONADE)], [write(identity)], [])

    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)

    assert "But it removed events.0 (its effects changed nothing in any test run), which revision 1 has" in \
        tool_replies(client)[1]
    assert result.contract == LEMONADE and result.kept == 1


def test_an_output_that_comes_out_the_same_in_every_test_run_is_a_warning_the_model_reads():
    no_market = lemonade(events=[])  # nothing earns: the winner is a tie-break

    result, replies = authored(no_market)

    flat = '[warning] outputs.winner: came out "Ana" in every test run (random, idle and edge-value agents)'
    assert result.ok and flat in replies[0] and flat in result.summary()
    assert "outputs.avg_price" not in replies[0]  # the agents' prices move it


def test_the_model_is_told_its_limits_and_the_tools_take_json_values():
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Done.", tool_calls=None),
                                                        finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

    fg_env.author("A game.", "openai:m", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=create))), budget={"calls": 1})

    instruction = seen["messages"][1]["content"]
    assert ("Limits: 8 saved revisions (each write_contract, edit_contract or start_from call that changes the "
            "contract saves one — put several edits in one edit_contract call") in instruction
    assert ("1 model calls, 600,000 tokens and 1,800 seconds in all. Each save is tested for up to 60 seconds"
            in instruction)
    tools = {tool["function"]["name"]: tool["function"]["parameters"] for tool in seen["tools"]}
    assert tools["write_contract"]["properties"]["contract"]["type"] == "object"
    assert "type" not in tools["edit_contract"]["properties"]["edits"]["items"]["properties"]["value"]


def test_one_edit_call_is_one_revision_and_a_save_that_changes_nothing_is_none():
    revised = {**WORKING, "name": "Pile", "clock": {**WORKING["clock"], "rounds": 12},
               "outputs": {**WORKING["outputs"], "left": "$world.stones"}}
    edits = edit(("name", "Pile"), ("clock.rounds", 12), ("outputs.left", "$world.stones"))
    client = FakeOpenAI([write(WORKING)], [edits], [edit(("name", "Pile"))], [write(revised)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    replies = tool_replies(client)
    assert replies[1].startswith("Saved revision 2: it works")
    assert replies[2] == replies[3] == ("Nothing changed: this is revision 2 as saved, not a new revision; it is "
                                        "kept")
    assert result.ok and result.kept == 2 and len(result.writes) == 2
    assert result.contract["name"] == "Pile" and result.contract["clock"]["rounds"] == 12


def test_a_contract_saved_before_is_not_tested_again(monkeypatch):
    from fg_env.authoring import workbench

    tests = []
    real = workbench.tested
    monkeypatch.setattr(workbench, "tested", lambda *args: tests.append(args) or real(*args))
    client = FakeOpenAI([write(WORKING)], [write({**WORKING, "name": "Other"})], [write(WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert len(tests) == 2 and result.working == [1, 2, 3] and result.kept == 3


def test_the_verdict_says_how_much_agents_read_and_warns_when_a_round_sends_a_great_many_tokens():
    crowd = lemonade(entities={f"s{n}": {"type": "seller", "name": f"Seller number {n}"} for n in range(150)},
                     clock={"rounds": 2, "unit": "hour"})

    small, small_replies = authored(AIMED)
    large, large_replies = authored(crowd)

    assert "; an agent reads ~" in small_replies[0] and " tokens a turn (its brief and update), ~" in small_replies[0]
    assert "\n  prompt: an agent reads ~" in small.summary() and "prompt tokens" not in small_replies[0]
    assert large.ok and "[warning] views: each round sends models about " in large_replies[0]
    assert "(150 agent turns of ~" in large_replies[0]


def test_a_brief_that_never_gives_an_agent_type_a_goal_is_a_warning():
    _, aimless = authored(LEMONADE)
    _, aimed = authored(AIMED)

    warning = "[warning] brief.roles.seller: the brief never says what a seller is trying to achieve"
    assert warning in aimless[0] and "brief.roles.seller" not in aimed[0]


def test_the_next_step_for_a_contract_that_consults_a_host_binds_the_host(tmp_path):
    out = str(tmp_path / "tavern.json")

    summary = fg_env.author("A tavern.", "openai:m", client=FakeOpenAI([write(TAVERN)], []), out=out).summary()

    assert (f"next: fg-env preview {out} " in summary and f" · run it with its hosts bound: fg_env.host.load({out!r}, "
            "hosts={'game_master': ...}).run()" in summary)
    assert "fg-env run" not in summary


def test_cli_refuses_a_brief_file_that_does_not_exist(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("fg_env.authoring.author.official_client", lambda *args: (_ for _ in ()).throw(AssertionError))

    code = main(["author", str(tmp_path / "shop_brief.txt"), "--model", "openai:m"])

    assert code == 1 and "error: no file '" in capsys.readouterr().err


def test_retries_never_wait_past_the_seconds_budget(monkeypatch):
    waits = []
    monkeypatch.setattr("fg_env.authoring.author.time.sleep", waits.append)
    client = FakeOpenAI(*[ProviderError(429)] * 5, [write(WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client, budget={"seconds": 3.5})

    assert waits == [1.0, 2.0] and result.stop == "error: ProviderError: HTTP 429"


def test_an_empty_anthropic_reply_is_retried(monkeypatch):
    waits, replies = [], [[], [SimpleNamespace(type="tool_use", id="t1", name="write_contract",
                                                input={"contract": WORKING})]]
    monkeypatch.setattr("fg_env.authoring.author.time.sleep", waits.append)

    def create(**kwargs):
        blocks = replies.pop(0) if replies else [SimpleNamespace(type="text", text="Done.")]
        return SimpleNamespace(content=blocks, stop_reason="tool_use" if blocks else "end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)))

    assert waits == [1.0] and result.ok and result.contract == WORKING and result.stop == "done"


def test_a_written_contract_is_advertised_as_an_object_but_json_text_still_saves():
    result = fg_env.author("A game.", "openai:m", client=FakeOpenAI([call("write_contract",
                                                                          contract=json.dumps(WORKING))], []))

    assert result.ok and result.contract == WORKING
