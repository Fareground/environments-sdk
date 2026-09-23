"""Agent memory: capture, notes, recall by relevance, recency and importance, forgetting, budgeted views,
reflections, and exact snapshots."""
import copy

import pytest

import fg_env
from fg_env import host
from fg_env.expr import Untrusted
from fg_env.host.stubs import StubRanker, StubWriter
from fg_env.mechanisms.memory import lexical_relevance

NOTEBOOK = {
    "name": "Notebook",
    "clock": {"rounds": 4},
    "types": {"person": {"agent": True}},
    "entities": {"ana": {"type": "person", "name": "Ana"}, "ben": {"type": "person", "name": "Ben"}},
    "records": {"chat": {"fields": {"text": "text"}, "show": "{author}: {text}"}},
    "actions": {"say": {"by": "person", "params": {"text": "text"}, "do": [{"post": "chat", "text": "$params.text"}],
                        "terminal": True}},
    "stages": [{"name": "talk", "turns": "sequential"}],
    "mechanisms": {"memory": {"kind": "mind", "mode": "memory", "who": "person", "half_life": 2, "limit": 12}},
}


def _with(**changes):
    contract = copy.deepcopy(NOTEBOOK)
    contract["mechanisms"]["memory"].update(changes)
    return contract


def talker(recalls):
    def participant(wake):
        if wake.round == 1 and wake.entity_id == "ana":
            assert wake.call("note", {"text": "The vault code is 4711."}).text == "Noted."
        if wake.round == 4 and wake.entity_id == "ana":
            recalls.append(wake.call("recall", {"query": "What was the vault code?"}).text)
        wake.call("say", {"text": f"{wake.name} mentions the weather in round {wake.round}."})
        wake.end()
    return participant


def _memory(env, entity_id="ana"):
    return env.entity(entity_id)["props"]["memory"]


def test_lexical_relevance_ranks_by_shared_rare_terms():
    scores = lexical_relevance("metro delays", ["The metro faces delays.", "Metro news", "Bakery prices"])
    assert scores[0] == 1.0 and 0 < scores[1] < 1 and scores[2] == 0.0
    assert lexical_relevance("the and of", ["the metro"]) == [0.0]


def test_agents_remember_what_they_did_and_read_and_recall_notes():
    recalls = []
    env = host.load(NOTEBOOK, seed=1)
    result = host.run(env, talker(recalls))
    assert result.status == "completed", result.error
    memory = _memory(env)
    kinds = {entry["kind"] for entry in memory}
    assert kinds == {"note", "did", "saw"}
    note = next(e for e in memory if e["kind"] == "note")
    assert isinstance(note["text"], Untrusted) and note["recalls"] == 1 and note["last"] == 4
    assert any(e["kind"] == "saw" and e["text"] == "Ben: «Ben mentions the weather in round 1.»" for e in memory)
    assert any(e["kind"] == "did" and e["text"].startswith("You did: say (text=«Ana") for e in memory)
    assert recalls[0].startswith("Memories about «What was the vault code?»:")
    assert "«The vault code is 4711.»" in recalls[0].splitlines()[1]
    assert result.stats["actions"] == 8  # note and recall used no action


def test_forgetting_keeps_the_strongest_memories_and_the_view_fits_its_budget():
    env = host.load(_with(limit=5, budget=20), seed=1)
    host.run(env, talker([]), rounds=4)
    memory = _memory(env)
    assert len(memory) == 5 and any(e["kind"] == "note" for e in memory)
    ids = [e["id"] for e in memory]
    assert ids == sorted(ids) and env.entity("ana")["props"]["memory_seq"] > 5
    shown = env.world.scope()
    from fg_env.expr import compile_expr
    items = compile_expr("$memories($entity(ana), 'memory')")(shown)
    assert sum(len(i["text"]) + len(i["label"]) + 2 for i in items) <= 20 * 4
    preview = env.preview("ana")["update"]
    assert "From your memory:" in preview


def test_recall_strengthens_what_it_returns_and_recency_decays():
    env = host.load(NOTEBOOK, seed=1)
    host.run(env, talker([]))
    memory = _memory(env)
    recalled = [e for e in memory if e["recalls"]]
    assert recalled and all(e["last"] == 4 for e in recalled)
    from fg_env.mechanisms.memory import _recency
    assert _recency({"at": 1, "last": 1}, 3, 2) == pytest.approx(0.5)
    assert _recency({"at": 1, "last": 3}, 3, 2) == 1.0


def test_host_relevance_and_reflections_are_recorded():
    ranker, writer = StubRanker(), StubWriter()
    contract = _with(relevance="host", host="ranker", reflect_every=2)
    env = host.load(contract, hosts={"ranker": ranker, "writer": writer}, seed=1)
    recalls = []
    result = host.run(env, talker(recalls))
    assert result.status == "completed", result.error
    assert len(ranker.calls) == 1 and "«The vault code is 4711.»" in recalls[0]
    reflections = [e for e in _memory(env) if e["kind"] == "reflection"]
    assert len(reflections) == 2 and all(isinstance(e["text"], Untrusted) for e in reflections)
    assert len(writer.calls) == 4  # two agents, rounds 2 and 4
    services = sorted(entry["service"] for entry in host.tape_of(env).values())
    assert services == ["ranker"] + ["writer"] * 4


def test_reflections_need_a_writer_unless_skipped():
    failed = host.run(host.load(_with(reflect_every=2), seed=1), talker([]))
    assert failed.status == "failed" and "needs the host 'writer'" in failed.error
    skipped = host.load(_with(reflect_every=2, reflect_fallback="skip"), seed=1)
    assert host.run(skipped, talker([])).status == "completed"
    assert not [e for e in _memory(skipped) if e["kind"] == "reflection"]


def test_memory_survives_a_snapshot_exactly():
    straight = host.load(NOTEBOOK, seed=2)
    host.run(straight, talker([]))
    first = host.load(NOTEBOOK, seed=2)
    host.run(first, talker([]), rounds=2)
    restored = host.restore(NOTEBOOK, first.snapshot())
    host.run(restored, talker([]))
    for person in ("ana", "ben"):
        assert _memory(restored, person) == _memory(straight, person)
    assert restored.snapshot()["entities"] == straight.snapshot()["entities"]


def test_a_plain_run_offers_note_and_recall_as_in_turn_tools_that_use_no_action():
    def participant(wake):
        wake.call("note", {"text": "Remember the lighthouse."})
        wake.end()

    env = fg_env.load(_with(capture=[]), seed=1)
    result = env.run(participant, rounds=1)
    assert result.status == "running" and result.error is None
    assert len(_memory(env)) == 1 and result.stats["actions"] == 0


def _errors(contract):
    return [str(i) for i in fg_env.check(contract) if i.severity == "error"]


def test_memory_config_and_actions_say_what_to_fix():
    old = copy.deepcopy(NOTEBOOK)
    old["mechanisms"]["memory"] = {"kind": "memory", "agents": "person"}
    assert any("'memory' is a mode of kind 'mind'" in e for e in _errors(old))
    assert any("`max_char` is not a field of `mind` mode `memory` → did you mean 'max_chars'?" in e
               for e in _errors(_with(max_char=40)))

    def op(*effects):
        return _errors({**NOTEBOOK, "events": [{"do": list(effects)}]})

    assert any("`mind.recall` needs `query`" in e for e in op({"mind": "memory", "action": "recall"}))
    assert any("'query' is not part of `mind.note`" in e for e in op({"mind": "memory", "action": "note", "text": "a", "query": "b"}))
    assert any("'remember' is not an action of memory (mind memory)" in e and "actions: note, recall" in e
               for e in op({"mind": "memory", "action": "remember"}))
    assert any('`note` is an action of the `mind` op: {"mind": "<mechanism>", "action": "note"' in e
               for e in op({"note": "memory", "text": "a"}))
    page = fg_env.guide("mind.memory")
    assert page.startswith("### `mind.memory`") and "- `recall` — takes `query`" in page and "- `reflect`" not in page
