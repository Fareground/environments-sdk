"""Personas written once at build time and recaps of long records: recorded, restored, replayed."""
import copy

import pytest

import fg_env
from fg_env import host
from fg_env.expr import Untrusted
from fg_env.host.stubs import StubWriter

MARKET = {
    "name": "Corner shop",
    "clock": {"rounds": 2},
    "types": {"shopper": {"agent": True, "props": {"age": 30}}},
    "population": [{"type": "shopper", "count": 4, "props": {"age": "20 + 10 * $i"}}],
    "mechanisms": {"lives": {"kind": "host", "mode": "personas", "who": "shopper",
                             "prompt": "A {age}-year-old shopper."}},
    "actions": {"browse": {"by": "shopper", "terminal": True}},
}


def _with(**changes):
    contract = copy.deepcopy(MARKET)
    contract["mechanisms"]["lives"].update(changes)
    return contract


def test_personas_are_written_once_at_load_and_carried_by_snapshots():
    writer = StubWriter()
    env = host.load(MARKET, hosts={"personas": writer}, seed=1)
    assert len(writer.calls) == 4 and writer.calls[0]["prompt"] == "A 30-year-old shopper."
    persona = env.world.entities["shopper_1"].properties["persona"]
    assert isinstance(persona, Untrusted) and persona.startswith("A 30-year-old shopper.")
    brief = env.preview("shopper_1")["brief"]
    assert f"Your persona: {persona}" in brief  # participant text formats in «»
    result = host.run(env, "random")
    assert result.status == "completed" and len(writer.calls) == 4

    restored = host.restore(MARKET, env.snapshot())
    assert restored.world.entities["shopper_1"].properties["persona"] == persona
    assert restored.world.entity_briefs == env.world.entity_briefs

    replay = host.load(MARKET, hosts=host.Hosts.replaying(host.tape_of(env)), seed=1)
    assert replay.world.entity_briefs == env.world.entity_briefs


def test_without_a_writer_personas_use_the_fallback_or_stop_clearly():
    env = fg_env.load(_with(fallback="Shopper aged {age}."), seed=1)
    result = env.run("random", rounds=1)
    assert result.status == "running" and result.error is None
    assert env.world.entities["shopper_2"].properties["persona"] == "Shopper aged 40."
    assert "Your persona: «Shopper aged 40.»" in env.preview("shopper_2")["brief"]
    with pytest.raises(fg_env.RunError, match="needs the host 'personas'"):
        host.load(MARKET, seed=1)
    failed = fg_env.load(MARKET, seed=1).run("random")
    assert failed.status == "failed" and "needs the host 'personas'" in failed.error


def test_persona_config_is_checked():
    with pytest.raises(fg_env.ContractError, match="not a declared type"):
        fg_env.load(_with(who="ghost"))
    with pytest.raises(fg_env.ContractError, match="prompt"):
        fg_env.load(_with(prompt="A {age shopper"))
    with pytest.raises(fg_env.ContractError, match="'personas' is a mode of kind 'host'"):
        fg_env.load({**MARKET, "mechanisms": {"lives": {"kind": "personas", "of": "shopper", "prompt": "A shopper."}}})
    with pytest.raises(fg_env.ContractError, match="`of` is not a field of `host` mode `personas`"):
        fg_env.load(_with(of="shopper"))


def test_personas_can_be_written_on_demand_with_the_write_action():
    stage = {"name": "shop", "turns": "sequential", "on_enter": [{"host": "lives", "action": "write"}]}
    contract = {**_with(fallback="Shopper aged {age}."), "stages": [stage]}
    env = fg_env.load(contract, seed=1)
    env.run("random", rounds=1)
    assert env.world.entities["shopper_4"].properties["persona"] == "Shopper aged 60."
    issues = [str(i)
              for i in fg_env.check({**contract,
                                     "events": [{"do": [{"host": "lives", "action": "write", "who": "x"}]}]})]
    assert any("'who' is not part of `host.write`" in i for i in issues)


BOARD = {
    "name": "Long board",
    "clock": {"rounds": 4},
    "types": {"member": {"agent": True}},
    "entities": {"ana": {"type": "member", "name": "Ana"}, "ben": {"type": "member", "name": "Ben"}},
    "records": {"board": {"fields": {"text": "text"}, "show": "{author}: {text}"}},
    "actions": {"post": {"by": "member", "params": {"text": "text"}, "do": [{"post": "board", "text": "$params.text"}],
                         "terminal": True}},
    "mechanisms": {"story": {"kind": "host", "mode": "recap", "record": "board", "every": 2}},
}


def poster(wake):
    wake.call("post", {"text": f"{wake.name} says something in round {wake.round}."})
    wake.end()


def test_recaps_summarise_only_new_entries_every_n_rounds():
    writer = StubWriter()
    env = host.load(BOARD, hosts={"writer": writer}, seed=1)
    updates = []
    result = host.run(env, lambda wake: (updates.append(wake.update), poster(wake)))
    assert result.status == "completed", result.error
    recaps = env.world.records("story")
    board = env.world.records("board")
    assert [r["round"] for r in recaps] == [2, 4]
    assert [r["through"] for r in recaps] == [board[3]["seq"], board[7]["seq"]]
    assert [len(call["entries"]) for call in writer.calls] == [4, 4]
    assert writer.calls[1]["previous"] == recaps[0]["text"] and isinstance(recaps[0]["text"], Untrusted)
    assert any("Story so far: «So far: 4 new entries" in u for u in updates)


def test_recaps_without_a_writer_extract_or_stop_clearly():
    extract = copy.deepcopy(BOARD)
    extract["mechanisms"]["story"]["fallback"] = "extract"
    env = host.load(extract, seed=1)
    assert host.run(env, poster).status == "completed"
    assert env.world.records("story")[0]["text"].startswith("Round 1, Ana: Ana says something in round 1.")
    failed = host.run(host.load(BOARD, seed=1), poster)
    assert failed.status == "failed" and "needs the host 'writer'" in failed.error
    hidden = copy.deepcopy(BOARD)
    hidden["records"]["board"]["visible"] = "$viewer.id == $it.author"
    with pytest.raises(fg_env.ContractError, match="would reveal it"):
        fg_env.load(hidden)
