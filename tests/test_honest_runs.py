"""A run says when it does not show what the environment is for: an action whose rule is broken for every choice,
agents that never act, and the tokens its hosts spent."""
import copy

import fg_env
from fg_env import host
from fg_env.host.stubs import StubEvaluator

from test_host_tape import PITCH, pitcher

LEDGER = {
    "name": "Ledger",
    "clock": {"rounds": 3},
    "world": {"booked": 0},
    "types": {"clerk": {"agent": True, "props": {"filed": 0}}},
    "entities": {"ann": {"type": "clerk"}, "bo": {"type": "clerk"}},
    "invariants": [{"expr": "$world.booked == $sum(clerk, $it.filed)", "why": "every filing is booked"}],
    "actions": {
        "file": {"by": "clerk", "do": ["$actor.filed += 1"]},  # forgets to book: breaks the invariant every time
        "split": {"by": "clerk", "params": {"n": {"type": "int", "min": 0, "max": 3}},
                  "do": ["$world.booked = $world.booked + 6 / $params.n - 6 / $params.n"]},  # fails only for n = 0
    },
    "outputs": {"booked": "$world.booked"},
}


def _filing(wake):
    wake.call("file")


def test_an_action_that_fails_every_time_it_applies_is_reported_and_marks_the_run_degraded():
    result = fg_env.run(LEDGER, _filing, seed=1)
    assert result.status == "completed" and not result.ok and result.stats["faulted_actions"] == 6
    finding = next(d for d in result.diagnostics if d["code"] == "action_always_faulted")
    assert finding["path"] == "actions.file" and "all 6 attempt(s)" in finding["message"]
    assert result.degraded == ["action_always_faulted"]
    assert "DEGRADED (action_always_faulted)" in result.summary().splitlines()[1]


def test_check_calls_an_action_broken_for_every_choice_an_error():
    issues = fg_env.check(LEDGER)
    broken = [i for i in issues if i.path == "actions.file"]
    assert broken and broken[0].severity == "error"
    assert not any(i.path == "actions.split" and i.severity == "error" for i in issues)


def test_a_rule_that_fails_only_for_some_choices_is_not_called_broken():
    choices = iter([0, 2, 0, 3, 1, 0])
    result = fg_env.run(LEDGER, lambda wake: wake.call("split", {"n": next(choices)}), seed=1)
    assert result.stats["faulted_actions"] == 3
    assert [d["code"] for d in result.diagnostics] == ["action_rule_failed"] and result.degraded == []


def test_agents_that_try_but_never_act_are_reported():
    def confused(wake):
        wake.call("file_report", {})  # not a tool

    result = fg_env.run(LEDGER, confused, seed=1)
    finding = next(d for d in result.diagnostics if d["code"] == "agents_never_acted")
    assert "6 turn(s)" in finding["message"] and "6 invalid" in finding["message"]
    assert result.degraded == ["agents_never_acted"]
    assert fg_env.run(LEDGER, "idle", seed=1).degraded == []  # a baseline that does nothing on purpose


class _MeteredJudge(StubEvaluator):
    """A judge that reports model usage like the reference adapters do."""

    def __init__(self):
        super().__init__()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def judge(self, request):
        self.usage["calls"] += 1
        self.usage["input_tokens"] += 400
        self.usage["output_tokens"] += 50
        return super().judge(request)


def test_host_tokens_count_in_the_run_stats_and_its_token_budget():
    judge = _MeteredJudge()
    result = host.load(PITCH, hosts={"judge": judge}, seed=1).run(pitcher)
    assert (result.stats["input_tokens"], result.stats["output_tokens"]) == (800, 100)
    capped = host.load(PITCH, hosts={"judge": _MeteredJudge()}, seed=1).run(pitcher, budget={"tokens": 300})
    assert (capped.ended_by, capped.rounds) == ("budget", 1)


def test_the_same_host_counted_by_two_runs_counts_each_token_once():
    hosts = host.Hosts({"judge": _MeteredJudge()})
    first = host.load(PITCH, hosts=hosts, seed=1).run(pitcher)
    second = host.load(copy.deepcopy(PITCH), hosts=hosts, seed=2).run(pitcher)
    assert first.stats["input_tokens"] == second.stats["input_tokens"] == 800
