"""Replay: a recorded LLM-and-host run plays again offline exactly, and a change shows up as its first divergence."""
import copy
import json
import time

import fg_env
from fg_env.__main__ import main
from fg_env.sdk import host
from fg_env.sdk.host.stubs import StubEvaluator

from test_exposures import TOWN
from test_host_tape import PITCH


def llm_like(wake):
    """Behaves like a model: reads the brief, the update and the tools, reports usage, makes a mistake, corrects it."""
    wake.brief
    update = wake.update
    tools = wake.tools_for("anthropic")
    wake.record_usage(llm_calls=1, input_tokens=len(update), output_tokens=7, cache_read_tokens=3)
    wake.call("pitch", {"speech": "we sell umbrellas"})
    wake.tools
    wake.call("pitch", {"text": f"Round {wake.round}: {len(tools)} tools to hand."})


def _recording(tmp_path):
    evaluator = StubEvaluator()
    env = host.load(PITCH, hosts={"judge": evaluator}, seed=1, exposures=True)
    result = env.run(llm_like)
    assert result.status == "completed" and len(evaluator.calls) == 2
    path = tmp_path / "pitch.jsonl"
    result.save(path)
    return result, path


def test_a_recorded_llm_and_host_run_replays_offline_identically(tmp_path):
    result, path = _recording(tmp_path)
    assert result.stats["invalid_calls"] == 2 and result.host_tape
    replayed = fg_env.trace(path).replay(PITCH)  # no host adapter and no model: the recording answers
    assert replayed.ok, replayed.message
    assert replayed.message == "the replay matched its recording: 2 turn(s), 5 event(s), completed after 2 round(s)"
    again = replayed.result
    assert again.events == result.events and again.outputs == result.outputs
    assert again.stats == result.stats and again.agent_stats == result.agent_stats
    assert again.exposures == json.loads(json.dumps(result.exposures)) and again.host_tape == result.host_tape


def test_changing_the_contract_text_reports_the_first_divergence_precisely(tmp_path):
    _, path = _recording(tmp_path)
    recording = fg_env.trace(path)
    briefed = dict(copy.deepcopy(PITCH), brief={"situation": "A pitch night."})
    brief = recording.replay(briefed).divergence
    assert brief["what"] == "brief" and brief["turn"] == 1 and brief["entity"] == "ana"
    assert brief["message"] == ('turn 1 (ana, round 1, stage play): the brief differs from the recording — '
                                'line 2 was "", now "A pitch night."')
    described = copy.deepcopy(PITCH)
    described["actions"]["pitch"]["description"] = "Pitch your company to the panel."
    assert recording.replay(described).message == ("turn 1 (ana, round 1, stage play): the tools offered differ from "
                                                   "the recording — the definition of 'pitch' changed")
    shorter = dict(copy.deepcopy(PITCH), clock={"rounds": 1})
    assert recording.replay(shorter).message == ('turn 1 (ana, round 1, stage play): the update differs from the '
                                                 'recording — line 1 was "Round 1 of 2 · play", now "Round 1 of 1 · play"')


def test_a_changed_call_result_is_reported_with_both_outcomes(tmp_path):
    _, path = _recording(tmp_path)
    changed = copy.deepcopy(PITCH)
    changed["actions"]["pitch"]["outcome"] = "Pitched."
    divergence = fg_env.trace(path).replay(changed).divergence
    assert divergence["what"] == "call" and divergence["call"] == 2 and divergence["tool"] == "pitch"
    assert divergence["got"] == {"ok": True, "ended": True, "text": "Pitched."}
    assert divergence["message"].startswith('turn 1 (ana, round 1, stage play): call 2, pitch {"text": "Round 1: ')
    assert 'returned ok (turn ended) "Pitched."; the recording had ok (turn ended) "' in divergence["message"]


def test_without_a_fallback_the_run_fails_at_the_divergence_and_with_one_it_plays_on(tmp_path):
    result, path = _recording(tmp_path)
    changed = copy.deepcopy(PITCH)
    changed["actions"]["pitch"]["description"] = "Pitch your company to the panel."
    player = fg_env.participants.replay(path)
    failed = host.load(changed, hosts=host.Hosts.replaying(result.host_tape), seed=1, exposures=True).run(player)
    assert failed.status == "failed" and failed.error == player.divergence["message"]
    unchecked = fg_env.participants.replay(path)
    assert "must record exposures" in host.load(PITCH, hosts=host.Hosts.replaying(result.host_tape), seed=1).run(
        unchecked).error
    carried = fg_env.trace(path).replay(changed, fallback="idle", hosts={"judge": StubEvaluator()})
    assert not carried.ok and carried.divergence["what"] == "tools"
    assert carried.result.status == "completed" and carried.result.stats["actions"] == 0


def test_a_replay_names_recorded_turns_it_never_reached(tmp_path):
    _, path = _recording(tmp_path)
    player = fg_env.participants.replay(path)
    tape = fg_env.trace(path).result.host_tape
    host.load(PITCH, hosts=host.Hosts.replaying(tape), seed=1, exposures=True).run(player, rounds=1)
    assert player.divergence is None
    assert player.unplayed()["message"] == ("turn 2 (ana, round 2, stage play): the replay never reached this turn "
                                            "(1 recorded turn(s) were not played)")


def test_recorded_timeouts_replay_exactly_without_a_clock():
    def slow(wake):
        wake.update
        time.sleep(0.05)
        wake.call("say", {"text": "late"})

    recorded = fg_env.run(TOWN, slow, seed=1, exposures=True, time_limit=0.01)
    assert recorded.stats["timeouts"] == 4 and recorded.exposures["wakes"][0]["steps"] == [["update"], ["timeout"]]
    replayed = fg_env.trace(recorded).replay(TOWN)
    assert replayed.ok, replayed.message
    assert replayed.result.events == recorded.events and replayed.result.stats == recorded.stats


def test_cli_replay_exits_zero_when_it_matches_and_one_on_a_divergence(tmp_path, capsys):
    _, path = _recording(tmp_path)
    same, changed = tmp_path / "same.json", tmp_path / "changed.json"
    same.write_text(json.dumps(PITCH))
    changed.write_text(json.dumps(dict(PITCH, brief={"situation": "A pitch night."})))
    assert main(["trace", str(path), "replay", str(same)]) == 0
    assert capsys.readouterr().out.startswith("OK: the replay matched its recording")
    assert main(["trace", str(path), "replay", str(changed), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["divergence"]["what"] == "brief"
