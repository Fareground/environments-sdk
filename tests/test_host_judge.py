"""The judge mechanism: a rubric judge scores every debate speech, the scores decide the winner, and a
replay without the evaluator is identical."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import fg_env
from fg_env import host
from fg_env.expr import Untrusted
from fg_env.host.stubs import StubEvaluator
from fg_env.mechanisms.judging import JudgeConfig, total_score
from fg_env.registry import config_data

DEBATE = Path(__file__).parents[1] / "examples" / "contracts" / "host" / "debate_judged.json"


def speaker(wake):
    other = "Blake" if wake.entity_id == "avery" else "Avery"
    wake.call("speak", {"text": f"Round {wake.round}: {other} ignores that free buses cut congestion costs."})
    wake.end()


def _without_stats(snapshot):
    return {key: value for key, value in snapshot.items() if key != "stats"}


def test_every_speech_is_scored_and_the_scores_decide_the_winner():
    evaluator = StubEvaluator()
    env = host.load(DEBATE, hosts={"judge": evaluator}, seed=3)
    updates = []
    result = host.run(env, lambda wake: (updates.append(wake.update), speaker(wake)))
    assert result.status == "completed", result.error
    verdicts = env.world.records("judge")
    assert len(verdicts) == 6 == len(evaluator.calls) == result.outputs["speeches"]
    config = JudgeConfig.model_validate(config_data(json.loads(DEBATE.read_text())["mechanisms"]["judge"]))
    for verdict in verdicts:
        assert verdict["total"] == total_score(verdict["scores"], config)
        assert isinstance(verdict["rationale"], Untrusted)
    for debater in ("avery", "blake"):
        earned = sum(v["total"] for v in verdicts if v["subject"] == debater)
        assert env.entity(debater)["props"]["score"] == pytest.approx(earned)
        assert env.props["judge_totals"][debater] == pytest.approx(earned)
    best = max(("avery", "blake"), key=lambda d: env.entity(d)["props"]["score"])
    assert result.outputs["winner"] == env.entity(best)["name"]
    assert any("Judged Avery:" in u and "«Scored by the stub evaluator.»" in u for u in updates)


def test_a_blind_judge_never_sees_names():
    evaluator = StubEvaluator()
    host.run(host.load(DEBATE, hosts={"judge": evaluator}, seed=3), speaker)
    for request in evaluator.calls:
        seen = json.dumps(request)
        assert "Avery" not in seen and "Blake" not in seen and "avery" not in seen
        assert request["subject"] in ("Participant A", "Participant B")
    assert "Participant B ignores" in evaluator.calls[0]["text"]
    assert evaluator.calls[1]["context"][0]["speaker"] == "Participant A"


def test_replaying_a_mid_run_snapshot_without_the_evaluator_is_identical():
    env = host.load(DEBATE, hosts={"judge": StubEvaluator()}, seed=5)
    host.run(env, speaker, rounds=1)
    middle = env.snapshot()
    finished = host.run(env, speaker)
    tape = host.tape_of(env)

    restored = host.restore(DEBATE, copy.deepcopy(middle), hosts=host.Hosts.replaying(tape))
    replayed = host.run(restored, speaker)
    assert replayed.outputs == finished.outputs
    assert replayed.events == finished.events
    assert _without_stats(restored.snapshot()) == _without_stats(env.snapshot())

    from_start = host.load(DEBATE, hosts=host.Hosts.replaying(tape), seed=5)
    assert host.run(from_start, speaker).outputs == finished.outputs

    final = host.restore(DEBATE, env.snapshot())
    assert final.props["judge_totals"] == env.props["judge_totals"]

    unrecorded = host.restore(DEBATE, copy.deepcopy(middle))
    failed = host.run(unrecorded, speaker)
    assert failed.status == "failed" and "needs the host 'judge'" in failed.error


PANEL = {
    "name": "Pitch panel",
    "clock": {"rounds": 1},
    "types": {"founder": {"agent": True, "props": {"points": 0}}},
    "entities": {"ana": {"type": "founder", "name": "Ana"}},
    "actions": {"pitch": {"by": "founder", "params": {"text": "text"},
                          "do": [{"host": "panel", "action": "judge", "text": "$params.text", "subject": "$actor"}],
                          "terminal": True}},
    "mechanisms": {"panel": {"kind": "host", "mode": "judge", "who": "founder", "into": "points", "aggregate": "median",
                             "panel": [{"name": "a"}, {"name": "b"}, {"name": "c", "host": "guest"}],
                             "criteria": {"quality": {"weight": 3}, "fit": {"scale": [0, 4]}}}},
    "outputs": {"points": "$entity(ana).points"},
}


def test_a_panel_is_aggregated_per_criterion():
    marks = {"a": (2, 4), "b": (9, 0), "c": (4, 1)}
    scores = lambda request: {"quality": marks[request["judge"]][0], "fit": marks[request["judge"]][1]}  # noqa: E731
    judges = StubEvaluator(scores)
    guest = StubEvaluator(scores)
    env = host.load(PANEL, hosts={"judge": judges, "guest": guest}, seed=1)
    result = host.run(env, lambda wake: (wake.call("pitch", {"text": "Umbrellas for clouds."}), wake.end()))
    assert result.status == "completed", result.error
    verdict = env.world.records("panel")[0]
    assert verdict["scores"] == {"quality": 4, "fit": 1}
    assert verdict["judges"] == ["a", "b", "c"] and len(judges.calls) == 2 and len(guest.calls) == 1
    expected = 10 * (3 * (4 - 1) / 9 + 1 * (1 - 0) / 4) / 4
    assert result.outputs["points"] == pytest.approx(expected, abs=1e-4)
    assert verdict["rationale"].startswith("a: ")


def test_judge_config_problems_are_reported_before_the_run():
    broken = copy.deepcopy(PANEL)
    broken["mechanisms"]["panel"]["criteria"] = {"quality": {"scale": [5, 1]}}
    with pytest.raises(fg_env.ContractError, match="scale"):
        fg_env.load(broken)
    staged = copy.deepcopy(PANEL)
    staged["mechanisms"]["panel"]["stage"] = "play"
    with pytest.raises(fg_env.ContractError, match="record"):
        fg_env.load(staged)


def _openai_client(message, usage=None):
    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


REQUEST = {"judge": "bench", "text": "A speech.", "criteria": [{"name": "logic", "min": 0, "max": 10}]}


def test_a_host_given_an_async_client_says_to_pass_the_sync_one():
    async def create(**kwargs):
        return None

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(fg_env.RunError, match="async client. Pass the sync client, openai.OpenAI()"):
        host.adapters.openai(client, "gpt-x").judge(REQUEST)


def test_a_host_recognises_an_openai_refusal():
    refusing = _openai_client(SimpleNamespace(content=None, refusal="I can't judge that."))
    with pytest.raises(host.HostError, match="declined"):
        host.adapters.openai(refusing, "gpt-x").judge(REQUEST)


def test_a_host_reads_usage_given_as_a_plain_dict():
    answer = SimpleNamespace(content=json.dumps({"scores": {"logic": 5}, "rationale": "ok"}), refusal=None)
    adapter = host.adapters.openai(_openai_client(answer, {"prompt_tokens": 40, "completion_tokens": 7}), "gpt-x")
    adapter.judge(REQUEST)
    assert (adapter.usage["input_tokens"], adapter.usage["output_tokens"], adapter.usage["unreported_usage"]) \
        == (40, 7, 0)


def test_a_host_bound_under_a_name_the_contract_never_consults_is_refused_at_load_naming_both():
    with pytest.raises(ValueError, match="'jdg' is bound, but the contract never consults it; it consults 'judge'"):
        host.load(DEBATE, hosts={"jdg": StubEvaluator()})
    assert host.load(DEBATE, hosts={"judge": StubEvaluator()}) is not None
