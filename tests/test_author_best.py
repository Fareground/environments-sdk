"""fg_env.author keeps the best revision, not the latest, and tests everything a model could reach: every action, every
view it may look at, every game-master effect; it stops honestly and within its time."""
import json
import time
from types import SimpleNamespace

import pytest
from test_author import WORKING, Cut, FakeOpenAI, call, edit, tool_replies, write
from test_author_honest import EXAMPLES, LEMONADE, authored, lemonade

import fg_env
from fg_env import host
from fg_env.host.stubs import StubGameMaster
from fg_env.participants import RandomAgent

TAVERN = json.loads((EXAMPLES / "host" / "tavern_gm.json").read_text())
#: The lemonade stand without its view, its winner and its market-clearing event.
GUTTED = {**{k: v for k, v in LEMONADE.items() if k != "views"}, "events": [],
          "outputs": {k: v for k, v in LEMONADE["outputs"].items() if k != "winner"}}


def test_a_working_revision_that_removes_parts_is_not_kept_and_the_model_is_sent_back_once(tmp_path):
    out = tmp_path / "stand.json"
    client = FakeOpenAI([write(LEMONADE)], [write(GUTTED)], [], [])

    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client, out=str(out))

    assert result.ok and result.contract == LEMONADE and json.loads(out.read_text()) == LEMONADE
    assert result.working == [1, 2] and result.kept == 1 and result.stop == "done"
    reply = tool_replies(client)[1]
    assert reply.startswith("Saved revision 2: it works")
    assert "it removed views.market, events.0, outputs.winner, which revision 1 has, so revision 1 stays kept" in reply
    sent_back = client.sent[3][-1]["content"]
    assert sent_back.startswith("Revision 2 works, but it removed views.market, events.0, outputs.winner")
    assert "save it again to confirm" in sent_back
    assert len(client.sent) == 4  # sent back once; stopping again ends the session
    assert ("kept revision 1 of 2; revision 2 was not kept: it removed views.market, events.0, "
            "outputs.winner") in result.summary()


def test_saving_the_removal_again_confirms_it():
    client = FakeOpenAI([write(LEMONADE)], [write(GUTTED)], [edit()], [])

    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)

    assert result.ok and result.contract == GUTTED and result.kept == 2 and result.working == [1, 2]
    assert tool_replies(client)[2] == "Revision 2 saved again unchanged: its removals are confirmed, and it is kept."
    assert "REMOVED since revision 1, the first that worked: views.market, events.0, outputs.winner" in result.summary()


def test_removals_are_measured_against_the_richest_working_revision_not_the_first():
    rich = lemonade(views={**LEMONADE["views"], "rivals": {"for": "seller", "of": "seller", "show": "{name} {price}"}},
                    outputs={**LEMONADE["outputs"], "total": {"expr": "$sum(seller, $it.earned)", "type": "number"}})
    client = FakeOpenAI([write(LEMONADE)], [write(rich)], [write(LEMONADE)], [], [])

    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)

    assert result.ok and result.contract == rich and result.kept == 2
    assert "it removed views.rivals, outputs.total, which revision 2 has" in tool_replies(client)[2]


def test_a_contract_in_which_no_action_can_ever_succeed_is_not_working():
    # Random agents now write real sentences, so the pitch's rule is what refuses them all: nothing ever happens.
    rules = lemonade(actions={
        "set_price": {**LEMONADE["actions"]["set_price"],
                      "when": [{"expr": "$params.price > 10", "why": "Prices must be above 10."}]},
        "pitch": {"by": "seller", "description": "Shout a slogan.",
                  "params": {"text": {"type": "text", "max_len": 100}},
                  "when": [{"expr": "$params.text == 'open sesame'", "why": "Wrong words."}],
                  "do": ["$actor.earned += 1"]}})

    result, _ = authored(rules)

    assert not result.ok and "agents_never_acted" in result.problem
    assert result.problem.startswith("a run with random agents (seed 1) shows agents_never_acted")


def test_random_agents_act_through_free_text():
    slogans = lemonade(actions={**LEMONADE["actions"], "pitch": {
        "by": "seller", "description": "Shout a slogan.", "params": {"text": {"type": "text", "max_len": 100}},
        "when": [{"expr": "$len($params.text) > 20", "why": "Too short."}], "do": ["$actor.earned += 1"]}})

    run = fg_env.load(slogans, seed=1).run({"*": RandomAgent(1)})

    pitched = [e for e in run.events if e["kind"] == "action" and e["data"]["action"] == "pitch"]
    assert pitched and all(e["data"]["success"] for e in pitched)


def test_a_view_offered_only_through_look_is_read_by_the_test_agents():
    views = {**LEMONADE["views"], "ledger": {"for": "seller", "title": "Ledger", "look": True, "of": "seller",
                                             "show": "{name}: {$round(100 / ($it.earned - $it.earned))}"}}

    result, _ = authored(lemonade(views=views))

    assert not result.ok and "views.ledger" in result.problem and "division by zero" in result.problem


def test_the_stub_game_master_applies_each_allowed_effect_within_its_bounds():
    env = host.load(TAVERN, hosts={"game_master": StubGameMaster()}, seed=1)

    run = env.run({"*": RandomAgent(1)})

    attempts = [e["data"]["fields"] for e in run.events if e["kind"] == "record" and e["data"]["record"] == "gm"]
    assert run.ok and attempts and not any(a["refused"] for a in attempts)
    changes = [change for a in attempts for change in a["changes"]]
    for applied in ("health", " gave ", "moved to", "alarm"):
        assert any(applied in change for change in changes), applied


def test_a_game_mastered_contract_that_breaks_on_any_effect_is_not_working():
    broken = {**TAVERN, "events": [{"phase": "end", "when": "$entity(mira).health != 8",
                                    "do": ["$world.alarm = (1 / 0) > 0"]}]}

    result, _ = authored(broken)

    assert not result.ok and "division by zero" in result.problem


def test_the_summary_names_the_stub_hosts_the_check_warnings_and_partial_testing(monkeypatch):
    game_master, _ = authored(TAVERN)
    warned, _ = authored(lemonade(actions={"set_price": {**LEMONADE["actions"]["set_price"], "do": []}}))
    monkeypatch.setattr("fg_env.authoring.testing.TEST_SECONDS", 1)
    long_run = lemonade(clock={"rounds": 100_000, "unit": "hour"})
    partly, _ = authored(long_run)

    assert game_master.ok and ("  hosts: game_master answered by the SDK's stand-in stubs in testing, not a model"
                               in game_master.summary())
    assert warned.ok and "  warnings:\n    [warning] outputs.avg_price: stayed 1.0" in warned.summary()
    summary = partly.summary()
    assert partly.ok and summary.startswith("built, PARTLY TESTED: Lemonade stand")
    assert "\n  PARTLY TESTED: tested at least" in summary and "of 100,000 rounds" in summary


def test_a_reply_cut_off_after_a_working_save_is_sent_back_once():
    client = FakeOpenAI([write(WORKING)], Cut([]), [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.ok and result.stop == "done" and len(client.sent) == 3
    assert "cut off at the output limit" in client.sent[2][-1]["content"]


def test_a_seconds_budget_stops_the_session():
    client = FakeOpenAI(*[[call("guide", part="effects")]] * 5)
    reply = client.create
    client.chat.completions.create = lambda **kwargs: (time.sleep(1), reply(**kwargs))[1]

    result = fg_env.author("A game.", "openai:m", client=client, budget={"seconds": 1.5})

    assert result.stop == "seconds" and result.usage["calls"] == 2
    assert fg_env.authoring.author.DEFAULT_BUDGET["seconds"] > 0


@pytest.mark.parametrize("budget, message", [({"seconds": 0}, "budget seconds must be a number of seconds above 0"),
                                             ({"minutes": 5}, "use tokens, calls and seconds")])
def test_a_bad_seconds_budget_says_how_to_fix_it(budget, message):
    with pytest.raises(ValueError, match=message):
        fg_env.author("A game.", "openai:m", client=FakeOpenAI(), budget=budget)


def test_anthropic_is_never_sent_whitespace_only_text_and_cache_writes_count_in_full():
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        blocks = [SimpleNamespace(type="text", text="\n\n"),
                  SimpleNamespace(type="tool_use", id=f"t{len(sent)}", name="check", input={})]
        return SimpleNamespace(content=blocks, stop_reason="tool_use", usage=SimpleNamespace(
            input_tokens=100, output_tokens=0, cache_creation_input_tokens=1000, cache_read_input_tokens=0))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)),
                           budget={"tokens": 2400})

    texts = [b for m in sent[-1]["messages"] for b in m["content"] if isinstance(b, dict) and b["type"] == "text"]
    assert all(b["text"].strip() for b in texts)
    # Each call spends 100 + 1,000 = 1,100, as a run's token budget counts it: the third passes 2,400.
    assert result.stop == "tokens" and result.usage["calls"] == 3 and result.usage["cache_write_tokens"] == 3000


def test_the_run_tool_does_not_show_the_host_tape():
    client = FakeOpenAI([write(TAVERN)], [call("run", seed=2)], [])

    fg_env.author("A tavern.", "openai:m", client=client)

    assert "attempts:" in tool_replies(client)[1] and "host_tape" not in tool_replies(client)[1]


def test_the_model_is_offered_the_engine_starters_and_can_start_from_one():
    client = FakeOpenAI([call("start_from", engine="negotiation")], [])

    result = fg_env.author("Two firms haggle over a supply deal.", "openai:m", client=client)

    instruction = client.sent[0][1]["content"]
    assert "- negotiation: " in instruction and "start_from" in instruction
    reply = tool_replies(client)[0]
    assert reply.startswith("Saved revision 1: it works") and '"name": ' in reply
    assert result.ok and result.contract == fg_env.engines.get("negotiation").materialized_source()


def test_a_seconds_budget_bounds_the_tool_calls_of_a_reply_and_the_tests_they_start():
    """One reply saves a slow contract and runs it twice: testing gets only the session's time left, and no tool call
    starts once it is up. Before, each save and run took its own full minute past the budget."""
    slow = {**WORKING, "clock": {**WORKING["clock"], "rounds": 90000}, "world": {"stones": 10 ** 9}}
    client = FakeOpenAI([write(slow), call("run", seed=1), call("run", seed=2)], [])

    started = time.monotonic()
    result = fg_env.author("A game.", "openai:m", client=client, budget={"seconds": 3})

    assert time.monotonic() - started < 20 and result.stop == "seconds"
    assert all(reply.startswith("Not done") for reply in tool_replies(client)[1:])


def test_a_starter_with_big_data_inputs_is_shown_abridged_and_writing_it_back_as_shown_is_refused():
    """contact_centre embeds 330,000 characters of input history; each later model call would resend them all."""
    from fg_env.authoring.workbench import MAX_RESULT, _abridged

    source = fg_env.engines.get("contact_centre").materialized_source()
    shown = _abridged(source)
    assert len(shown) < 2 * MAX_RESULT and "the saved contract holds them all" in shown
    as_shown = json.loads(shown.split("\n[shown with only")[0])
    assert [i for i in fg_env.check(as_shown, rounds=0) if i.severity == "error"]
    small = fg_env.engines.get("negotiation").materialized_source()
    assert json.loads(_abridged(small)) == small


def test_a_shortened_run_or_an_output_made_constant_must_be_confirmed_and_settings_changes_are_named():
    """A working edit that cut the run to one round, or rewrote an output to a constant, was kept silently, and the
    summary named no change (audit 9 author M6, L8)."""
    shorter = {**LEMONADE, "clock": {**LEMONADE["clock"], "rounds": 1}}
    client = FakeOpenAI([write(LEMONADE)], [write(shorter)], [], [])
    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)
    assert result.kept == 1 and "clock.rounds (shortened from" in tool_replies(client)[1]
    output = next(iter(LEMONADE["outputs"]))
    constant = {**LEMONADE, "outputs": {**LEMONADE["outputs"], output: "0"}}
    client = FakeOpenAI([write(LEMONADE)], [write(constant)], [], [])
    result = fg_env.author("A lemonade stand duel.", "openai:m", client=client)
    assert result.kept == 1 and f"outputs.{output} (now a constant)" in tool_replies(client)[1]
    briefer = {**LEMONADE, "brief": {**LEMONADE.get("brief", {}), "situation": "A hot day."}}
    client = FakeOpenAI([write(LEMONADE)], [write(briefer)], [], [])
    assert "brief " in fg_env.author("A lemonade stand duel.", "openai:m", client=client).summary()
