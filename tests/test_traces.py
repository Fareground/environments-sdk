"""Traces: results saved and loaded, and reading what every agent read, was offered, called and was told."""
import json

import pytest

import fg_env
from fg_env.__main__ import main

from test_exposures import TOWN, reader
from test_runtime import SHOP

TOKENS_IN, TOKENS_OUT = 100, 10


def overreacher(wake):
    """Reads everything, asks for far too much espresso, corrects itself, and reports model usage."""
    wake.brief
    wake.update
    wake.tools_for("anthropic")
    wake.record_usage(llm_calls=1, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)
    if not wake.call("buy", {"offer": "espresso", "qty": 99}).ok:
        wake.call("buy", {"offer": "latte", "qty": 1})
    wake.end()


def _recorded():
    return fg_env.run(SHOP, overreacher, seed=3, exposures=True)


@pytest.mark.parametrize("name", ["run.json", "run.jsonl"])
def test_a_result_saved_as_json_or_json_lines_loads_back_unchanged(tmp_path, name):
    result = _recorded()
    path = tmp_path / name
    result.save(path)
    loaded = fg_env.RunResult.load(path)
    assert loaded.to_dict() == json.loads(json.dumps(result.to_dict()))
    assert fg_env.analysis.trace(path).overview().data == fg_env.analysis.trace(result).overview().data


def test_loading_something_that_is_not_a_saved_result_says_what_to_pass(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"name": "not a result"}))
    with pytest.raises(ValueError, match="is not a run result: it has no status"):
        fg_env.RunResult.load(path)
    lines = tmp_path / "other.jsonl"
    lines.write_text('{"event": {}}\n')
    with pytest.raises(ValueError, match="does not start with a result header"):
        fg_env.RunResult.load(lines)


def test_a_trace_needs_a_run_that_recorded_exposures():
    with pytest.raises(ValueError, match="run it with exposures=True"):
        fg_env.analysis.trace(fg_env.run(SHOP, overreacher, seed=3))


def test_the_overview_counts_each_agents_turns_calls_invalid_calls_and_tokens():
    result = _recorded()
    view = fg_env.analysis.trace(result).overview()
    rows = {row["entity"]: row for row in view.data["agents"]}
    assert list(rows) == ["shopper_1", "shopper_2", "shopper_3", "shopper_4"]
    for entity, row in rows.items():
        stats = result.agent_stats[entity]
        assert (row["turns"], row["calls"], row["invalid"]) == (stats["wakes"], stats["calls"], stats["invalid_calls"])
        assert (row["input_tokens"], row["output_tokens"]) == (TOKENS_IN * row["turns"], TOKENS_OUT * row["turns"])
        assert row["invalid_rate"] == round(row["invalid"] / row["calls"], 3)
    assert view.data["totals"]["calls"] == result.stats["calls"]
    assert view.data["run"]["wakes"] == 12 and "shopper_1" in str(view)


def test_a_turn_shows_what_the_agent_read_the_tools_it_had_and_every_call_with_its_result():
    recording = fg_env.analysis.trace(_recorded())
    [wake] = recording.turn(0).data
    assert wake["entity"] == "shopper_1" and wake["round"] == 1 and wake["stage"] == "shop"
    assert wake["brief"].startswith("# Corner shop") and "You have $30.00." in wake["update"]
    assert wake["tools"] == ["buy", "end_turn"]
    assert [tool["name"] for tool in wake["tool_sets"][0]] == ["buy", "end_turn"]
    wrong, right, done = wake["calls"]
    assert wrong["error"] == "invalid" and "qty must be at most 5" in wrong["result"]
    assert right == {"tool": "buy", "args": {"offer": "latte", "qty": 1}, "ok": True, "ended": False,
                     "result": "You bought 1 × Latte for $5.00."}
    assert done["tool"] == "end_turn" and done["ended"]
    assert wake["usage"] == {"llm_calls": 1, "input_tokens": TOKENS_IN, "output_tokens": TOKENS_OUT}
    text = str(recording.turn(0))
    assert "--- update (" in text and "1. buy" in text and "   You bought 1 × Latte for $5.00." in text
    assert recording.turn("shopper_2", 1).data == recording.turn(1).data


def test_asking_for_a_turn_that_does_not_exist_says_which_do():
    recording = fg_env.analysis.trace(_recorded())
    with pytest.raises(ValueError, match="no wake 99: this run has wakes 0 to 11"):
        recording.turn(99)
    with pytest.raises(ValueError, match="'nobody' was never woken in this run"):
        recording.turn("nobody", 1)
    with pytest.raises(ValueError, match=r"give the round too, like turn\('shopper_1', 1\)"):
        recording.turn("shopper_1")
    with pytest.raises(ValueError, match=r"was not woken in round 9 \(rounds it was woken: 1, 2, 3\)"):
        recording.turn("shopper_1", 9)


def test_timeline_search_invalid_and_agent_find_what_agents_read_and_wrote():
    recording = fg_env.analysis.trace(_recorded())
    timeline = recording.timeline("shopper_1").data
    assert [row["round"] for row in timeline] == [1, 2, 3]
    assert [call["tool"] for call in timeline[0]["calls"]] == ["buy", "buy", "end_turn"]
    hits = recording.search("ESPRESSO").data
    places = {(hit["wake"], hit["where"]) for hit in hits}
    assert (0, "update") in places and (0, "call 1 buy arguments") in places and (0, "tools") in places
    assert sum(1 for hit in hits if hit["entity"] == "shopper_1" and hit["where"] == "brief") == 0
    assert recording.search("Corner shop").data[0]["where"] == "brief"
    assert len([hit for hit in recording.search("Corner shop").data if hit["entity"] == "shopper_1"]) == 1
    refused = recording.invalid().data
    assert refused and all(row["tool"] == "buy" and row["error"] == "invalid" and row["correction"] for row in refused)
    assert len(refused) == sum(row["invalid"] for row in recording.overview().data["agents"])
    agent = recording.agent("shopper_1").data
    assert agent["tools"]["end_turn"] == {"calls": 3, "ok": 3, "refused": 0}
    assert "tool" in str(recording.agent("shopper_1")) and "nowhere" in str(recording.search("zebra"))
    with pytest.raises(ValueError, match="search needs some text"):
        recording.search("  ")


def test_each_wake_keeps_the_steps_its_participant_took_in_order():
    def late_reader(wake):
        wake.tools
        wake.call("look", {"view": "board"})
        wake.update
        wake.record_usage(input_tokens=5)
        wake.tools  # a second read changes nothing and is not a step

    wake = fg_env.run(TOWN, late_reader, seed=1, exposures=True).exposures["wakes"][0]
    assert wake["steps"] == [["tools"], ["call", "look", {"view": "board"}], ["update"], ["usage", {"input_tokens": 5}]]
    assert wake["brief"] is None


def test_cli_records_a_trace_and_reads_it(tmp_path, capsys):
    contract, out = tmp_path / "town.json", tmp_path / "town.jsonl"
    contract.write_text(json.dumps(TOWN))
    assert main(["run", str(contract), "--seed", "1", "--trace", str(out)]) == 0
    capsys.readouterr()
    assert fg_env.analysis.trace(out).result.status == "completed"
    assert main(["trace", str(out)]) == 0 and "agent" in capsys.readouterr().out
    assert main(["trace", str(out), "turn", "ann", "1"]) == 0 and "Wake 0: ann" in capsys.readouterr().out
    assert main(["trace", str(out), "timeline", "bo", "--json"]) == 0
    assert all(row["entity"] == "bo" for row in json.loads(capsys.readouterr().out))
    assert main(["trace", str(out), "search", "Say", "something"]) == 0 and "tools" in capsys.readouterr().out
    assert main(["trace", str(out), "turn", "first"]) == 1
    assert "usage: fg-env trace FILE turn WAKE" in capsys.readouterr().err
    assert main(["trace", str(out), "agent"]) == 1


def test_a_trace_from_a_run_with_reads_matches_the_exposure_log():
    result = fg_env.run(TOWN, reader, seed=1, exposures=True)
    recording = fg_env.analysis.trace(result.to_dict())
    assert recording.entities == ["ann", "bo"]
    assert recording.turn(0).data[0]["views"][1] == {"name": "board", "look": True, "text": "Board:\nNothing yet."}
