"""A run says when it does not show how its agents play — per agent, not only when every agent failed — and the
engine never silently loses what an agent should see or do."""
import itertools
import json
import time
from types import SimpleNamespace as NS

import pytest

import fg_env
from fg_env import participants
from fg_env.__main__ import main

CONNECT_FOUR = "examples/contracts/connect_four.json"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)


class Scripted:
    """An Anthropic-style client whose reply to each request is ``reply(request)``."""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.messages = self
        self._ids = itertools.count()

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        return self.reply(request, self)

    def tool(self, name, args, stop="tool_use"):
        return _response([NS(type="tool_use", id=f"t{next(self._ids)}", name=name, input=args)], stop)


def _response(content, stop):
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=100, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _first_legal(request, client):
    tool = next(t for t in request["tools"] if t["name"] not in ("inspect", "look", "end_turn"))
    schema = tool["input_schema"]
    args = {name: spec["enum"][0] for name, spec in schema.get("properties", {}).items()
            if name in schema.get("required", []) and "enum" in spec}
    return client.tool(tool["name"], args)


def _player(reply):
    return participants.anthropic(Scripted(reply), "m")


@pytest.mark.parametrize("yellow", [
    lambda request, client: _response([], "refusal"),
    lambda request, client: client.tool("drop_disc", {"column": 1}),
    lambda request, client: _response([NS(type="text", text="I would play the centre.")], "end_turn"),
], ids=["refuses", "unknown tool", "text only"])
def test_one_seat_that_never_acts_degrades_the_run(yellow):
    result = fg_env.run(CONNECT_FOUR, {"red": _player(_first_legal), "yellow": _player(yellow)}, seed=1)
    assert result.degraded == ["agents_never_acted"] and not result.ok
    [found] = [d for d in result.diagnostics if d["code"] == "agents_never_acted"]
    assert found["message"].startswith("yellow took no action")
    assert result.summary().splitlines()[1].startswith("DEGRADED (agents_never_acted)")


def test_a_seat_whose_turns_mostly_fail_degrades_the_run():
    def sometimes(request, client):  # the first reply of every third turn acts; every other turn only errs
        turn = sum(1 for r in client.requests if len(r["messages"]) == 1)
        return _first_legal(request, client) if turn % 3 == 0 else client.tool("drop_disc", {})

    result = fg_env.run(CONNECT_FOUR, {"red": _player(_first_legal), "yellow": _player(sometimes)}, seed=1)
    assert result.agent_stats["yellow"]["actions"] > 0
    assert result.agent_stats["yellow"]["failed_turns"] > result.agent_stats["yellow"]["wakes"] / 2
    assert result.degraded == ["agents_mostly_failed"] and not result.ok
    [found] = [d for d in result.diagnostics if d["code"] == "agents_mostly_failed"]
    assert "yellow" in found["message"] and "red" not in found["message"]


def test_sound_runs_exit_zero_and_degraded_runs_exit_three(tmp_path, capsys):
    healthy = fg_env.run(CONNECT_FOUR, _player(_first_legal), seed=1)
    assert healthy.ok and healthy.degraded == []
    assert main(["run", CONNECT_FOUR, "--seed", "1"]) == 0
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(LEDGER))
    assert main(["run", str(ledger), "--seed", "1", "--agent", "idle"]) == 0  # doing nothing on purpose is sound
    assert main(["run", str(ledger), "--seed", "1", "--agent", "policy:overfile"]) == 3
    assert "DEGRADED (agents_never_acted)" in capsys.readouterr().out


def test_a_run_its_budget_ended_is_still_ok():
    result = fg_env.run(CONNECT_FOUR, _player(_first_legal), seed=1, budget={"calls": 3})
    assert result.ended_by == "budget" and result.ok


LEDGER = {
    "name": "Ledger", "clock": {"rounds": 3},
    "types": {"clerk": {"agent": True, "props": {"filed": 0}}}, "entities": {"ann": {"type": "clerk"}},
    "world": {"shelf": 3},
    "actions": {"file": {"by": "clerk", "params": {"n": {"type": "int", "min": 1, "max": 9}},
                         "do": [{"if": "$params.n > $world.shelf", "then": [{"fail": "the shelf holds 3"}]},
                                "$actor.filed += $params.n"]}},
    "policies": {"overfile": {"rules": [{"do": "file", "with": {"n": 5}}]}},
    "outputs": {"filed": "$sum(clerk, $it.filed)"},
}


def test_wake_me_is_a_copy_the_agent_cannot_change_the_world_through():
    contract = {"name": "Me", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"hand": {"type": "list", "default": [1, 2]}}}},
                "entities": {"a": {"type": "p"}}, "actions": {"noop": {"by": "p"}}, "outputs": {"hand": "$entity(a).hand"}}

    def cheat(wake):
        wake.me["hand"].extend(["ace"] * 50)

    assert fg_env.run(contract, cheat, seed=1).outputs["hand"] == [1, 2]


def test_agents_poking_each_other_awake_never_fail_the_run():
    contract = {"name": "Poke", "clock": {"rounds": 2}, "world": {"pokes": 0},
                "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"poke": {"by": "p", "params": {"target": {"type": "entity", "of": "p", "where": "$it != $actor"}},
                                     "do": ["$world.pokes += 1", {"wake": "$params.target", "why": "poked", "now": True}]}},
                "outputs": {"pokes": "$world.pokes"}}

    def poke(wake):
        wake.call("poke", {"target": "b" if wake.entity_id == "a" else "a"})

    result = fg_env.run(contract, poke, seed=1)
    assert result.status == "completed" and result.ok, result.error
    assert result.stats["reactions"] >= 4 and result.outputs["pokes"] > result.stats["reactions"]


def test_a_message_addressed_to_an_agent_is_never_lost_in_a_busy_update():
    crowd = {f"p{i}": {"type": "p"} for i in range(200)}
    contract = {"name": "Crowd", "clock": {"rounds": 2}, "types": {"p": {"agent": True}}, "entities": crowd,
                "records": {"dm": {"fields": {"text": "text"}}},
                "actions": {"dm": {"by": "p", "params": {"to": {"type": "entity", "of": "p"}, "text": "text"},
                                   "do": [{"post": "dm", "text": "$params.text", "to": ["$params.to"]}]},
                            "work": {"by": "p"}},
                "events": [{"at": 1, "phase": "end", "say": "The market closes early tomorrow."}],
                "outputs": {"n": "$count(p)"}}

    def play(wake):
        if wake.entity_id == "p1":
            wake.call("dm", {"to": "p0", "text": "URGENT: meet at dawn"})
        else:
            wake.call("work", {})

    env = fg_env.load(contract, seed=1)
    env.run(play, rounds=1)
    update = env.preview("p0")["update"]
    assert "URGENT: meet at dawn" in update and "The market closes early tomorrow." in update
    assert "more items not shown" in update and "p199: work." in update


def _tag_contract(turns):
    return {"name": "Tagging", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "props": {"tagged": {"type": "list", "default": []}}},
                      "t": {"props": {"hits": 0}}},
            "entities": {"a": {"type": "p"}, "x": {"type": "t"}, "y": {"type": "t"}, "z": {"type": "t"}},
            "stages": [{"name": "tag", "turns": turns, "max_actions": 3}],
            "actions": {"tag": {"by": "p", "params": {"target": {"type": "entity", "of": "t",
                                                                  "where": "not ($it.id in $actor.tagged)"}},
                                "do": ["$actor.tagged += $params.target.id", "$params.target.hits += 1"]}},
            "policies": {"all": {"repeat": True, "rules": [
                {"when": "$count(t, not ($it.id in $actor.tagged)) > 0", "do": "tag",
                 "with": {"target": "$best($filter(t, not ($it.id in $actor.tagged)), $it.id)"}}]}},
            "outputs": {"hits": "$sum(t, $it.hits)"}}


@pytest.mark.parametrize("turns", ["sequential", "simultaneous"])
def test_a_repeating_policy_sees_its_own_sealed_choices(turns):
    result = fg_env.run(_tag_contract(turns), "policy:all", seed=1)
    assert result.outputs["hits"] == 3 and result.diagnostics == []


def test_a_repeating_policy_stopped_by_a_refusal_says_so():
    contract = _tag_contract("sequential")
    contract["policies"]["all"]["rules"][0]["when"] = "true"  # keeps trying once every target is tagged
    contract["policies"]["all"]["rules"][0]["with"] = {"target": "x"}
    result = fg_env.run(contract, "policy:all", seed=1)
    assert result.outputs["hits"] == 1
    [found] = [d for d in result.diagnostics if d["code"] == "policy_repeat_refused"]
    assert found["path"] == "policies.all.rules[0]" and "acted 1 time(s) and was refused 1 time(s)" in found["message"]


def test_auto_skips_sealed_turns_with_nothing_legal():
    contract = {"name": "Night", "clock": {"rounds": 2},
                "types": {"p": {"agent": True, "props": {"wolf": False, "kills": 0}}},
                "entities": {"a": {"type": "p", "props": {"wolf": True}}, "b": {"type": "p"}, "c": {"type": "p"}},
                "stages": [{"name": "night", "turns": "simultaneous", "auto": True, "actions": ["hunt"]}],
                "actions": {"hunt": {"by": "p", "when": ["$actor.wolf"], "params": {"k": {"type": "int", "min": 1, "max": 3}},
                                     "do": "$actor.kills += $params.k"}},
                "outputs": {"kills": "$sum(p, $it.kills)"}}
    woken = []

    def wolf(wake):
        woken.append(wake.entity_id)
        wake.call("hunt", {"k": 2})

    result = fg_env.run(contract, wolf, seed=1)
    assert woken == ["a", "a"] and result.outputs["kills"] == 4
    assert result.stats["auto_turns"] == 4 and result.stats["wakes"] == 2


def test_parallel_runs_sharing_a_judge_each_count_their_own_tokens():
    from fg_env.host.adapters import anthropic as judge_on

    def verdict(request, client):
        time.sleep(0.002)  # overlapping calls
        text = json.dumps({"scores": {"logic": 7, "evidence": 6, "rebuttal": 5}, "rationale": "ok"})
        return NS(content=[NS(type="text", text=text)], stop_reason="end_turn",
                  usage=NS(input_tokens=1000, output_tokens=50))

    def speaker(wake):
        wake.call("speak", {"text": f"Round {wake.round}: free buses cut congestion costs."})

    shared = fg_env.host.Hosts({"judge": judge_on(Scripted(verdict), "m")})
    result = fg_env.experiment("examples/contracts/host/debate_judged.json", runs=6, workers=4, hosts=shared,
                               participants=speaker)
    runs = [run for arm in result.arms.values() for run in arm.runs]
    assert [run.stats["input_tokens"] for run in runs] == [6000] * 6


def test_a_judge_answer_cut_off_at_its_limit_says_so():
    from fg_env.host.adapters import anthropic as judge_on

    def cut(request, client):
        return NS(content=[NS(type="text", text='{"scores": {"logic": 7, "evid')], stop_reason="max_tokens",
                  usage=NS(input_tokens=10, output_tokens=16000))

    env = fg_env.host.load("examples/contracts/host/debate_judged.json", hosts={"judge": judge_on(Scripted(cut), "m")},
                           seed=1)
    result = env.run(lambda wake: wake.call("speak", {"text": "Free buses cut congestion."}))
    assert result.status == "failed" and "cut off at its output limit (max_tokens=16000)" in result.error


def test_a_turn_that_runs_out_of_model_calls_is_counted_and_reported():
    agent = participants.anthropic(Scripted(lambda request, client: client.tool("drop_disc", {})), "m", max_steps=3)
    result = fg_env.run(CONNECT_FOUR, {"red": _player(_first_legal), "yellow": agent}, seed=1)
    assert result.agent_stats["yellow"]["out_of_steps"] == result.agent_stats["yellow"]["wakes"] > 0
    assert agent.usage.out_of_steps == result.agent_stats["yellow"]["out_of_steps"]
    found = {d["code"]: d for d in result.diagnostics}
    assert "yellow" in found["out_of_steps"]["message"] and "max_steps" in found["out_of_steps"]["fix"]


def test_an_unchanged_view_is_named_not_dropped():
    contract = {"name": "Board", "clock": {"rounds": 2}, "world": {"price": 5},
                "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}}, "actions": {"wait": {"by": "p"}},
                "views": {"price_board": {"for": "p", "show": "Price: {$world.price}", "only_changes": True}},
                "outputs": {"price": "$world.price"}}
    updates = []
    fg_env.run(contract, lambda wake: updates.append(wake.update), seed=1)
    assert "Price: 5" in updates[0]
    assert "Price: 5" not in updates[1] and "Price board: unchanged since your last turn." in updates[1]
