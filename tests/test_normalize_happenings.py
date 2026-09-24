"""Earlier forms of happenings and time load as events and the current fields: each rule rewrites its construct, leaves
the current form alone, and runs the same way."""
import pytest

import fg_env
from fg_env.contract.normalize import normalize

AGENT = {"agent": True, "props": {"bid": 0}}


def _normal(data):
    out, _ = normalize(data)
    again, notes = normalize(out)
    assert again == out and notes == []  # idempotent
    return out


def test_triggers_become_change_events_after_the_declared_events():
    out = _normal({"events": [{"do": ["$world.a = 1"]}],
                   "triggers": [{"when": "$world.a == 1", "do": ["$world.b = 1"], "once": True, "arms": ["t"]}]})
    assert "triggers" not in out
    assert out["events"][1] == {"on": "change", "when": "($arm in ['t']) and ($world.a == 1)",
                                "do": ["$world.b = 1"], "once": True}


def test_event_timing_becomes_on_and_when():
    out = _normal({"events": [{"phase": "end", "at": 3, "when": "$world.x", "do": ["$world.y = 1"]},
                              {"every": 7, "do": []}, {"at": [1, 4], "do": []}]})
    assert out["events"][0] == {"on": "round.end", "when": "($round == 3) and ($world.x)", "do": ["$world.y = 1"]}
    assert out["events"][1]["when"] == "($round - 1) % 7 == 0"
    assert out["events"][2]["when"] == "$round in [1, 4]"


def test_an_event_loop_moves_into_its_do():
    out = _normal({"events": [{"each": "ant", "as": "a", "where": "$a.alive", "order": "random",
                               "do": ["$a.x += 1"]},
                              {"each": "cell", "sync": True, "do": "$it.on = true"}]})
    assert out["events"][0] == {"do": [{"each": "$shuffle(ant)", "as": "a", "where": "$a.alive",
                                        "do": ["$a.x += 1"]}]}
    assert out["events"][1] == {"do": [{"each": "cell", "sync": True, "do": ["$it.on = true"]}]}


def test_stage_and_type_hooks_become_events_on_their_anchors():
    out = _normal({"stages": [{"name": "s", "on_enter": ["$world.a = 1"], "on_exit": "$world.b = 1",
                               "on_idle": ["$world.c = 1"], "on_timeout": ["$world.d = 1"], "atomic": True,
                               "time_limit": 30}],
                   "types": {"t": {"on_create": ["$it.x = 1"], "on_create_at_build": False,
                                   "on_remove": ["$world.n -= 1"]}}})
    assert out["stages"] == [{"name": "s", "valid": "true"}]
    assert out["events"] == [
        {"on": "stage.s.start", "do": ["$world.a = 1"]},
        {"on": "stage.s.end", "do": ["$world.b = 1"]},
        {"on": "stage.s.turn", "when": "$timed_out", "do": ["$world.d = 1"]},
        {"on": "stage.s.turn", "when": "not $acted and not $timed_out", "do": ["$world.c = 1"]},
        {"on": "create.t", "when": "$round >= 1", "do": ["$it.x = 1"]},
        {"on": "remove.t", "do": ["$world.n -= 1"]},
    ]


def test_action_and_view_fields():
    out = _normal({"actions": {"a": {"private": True, "chance": 0.5, "do": ["$x = 1"], "otherwise": ["$x = 2"],
                                     "tool": "grp"}},
                   "views": {"v": {"stages": ["s"], "when": "$world.on", "only_changes": True, "show": "x"}},
                   "clock": {"mode": "rounds", "rounds": 3, "tick": 1}})
    assert out["actions"]["a"] == {"announce": False, "do": [{"if": "$chance(0.5)", "then": ["$x = 1"],
                                                             "else": ["$x = 2"]}]}
    assert out["views"]["v"] == {"when": "($stage in ['s']) and ($world.on)", "show": "x"}
    assert out["clock"] == {"rounds": 3}


def test_a_contract_in_the_earlier_form_runs_like_the_current_form():
    """A sealed auction resolved in a stage's on_exit, with private bids."""
    earlier = {"name": "Auction", "clock": {"rounds": 2}, "world": {"top": 0},
               "types": {"p": AGENT}, "population": [{"type": "p", "count": 3}],
               "actions": {"bid": {"by": "p", "private": True, "params": {"amount": {"type": "int", "min": 0,
                                                                                     "max": 9}},
                                   "do": ["$actor.bid = $params.amount"]}},
               "stages": [{"name": "bid", "turns": "simultaneous", "on_exit": ["$world.top = $max(p, $it.bid)"]}]}
    current = {**earlier,
               "actions": {"bid": {**earlier["actions"]["bid"], "announce": False}},
               "stages": [{"name": "bid", "turns": "simultaneous"}],
               "events": [{"on": "stage.bid.end", "do": ["$world.top = $max(p, $it.bid)"]}]}
    del current["actions"]["bid"]["private"]
    assert fg_env.run(earlier, seed=5).to_dict() == fg_env.run(current, seed=5).to_dict()


@pytest.mark.parametrize(("patch", "fix"), [
    ({"clock": {"mode": "continuous", "horizon": 10}}, "continuous clock was removed"),
    ({"stages": [{"name": "s", "auto": True}]}, "every turn wakes its agent"),
    ({"stages": [{"name": "s", "on_wake": ["$world.n = 1"]}]}, "stage.<name>.start"),
    ({"actions": {"go": {"by": "p", "duration": 2}}}, "continuous clock was removed"),
])
def test_what_has_no_current_form_is_refused_with_what_to_write(patch, fix):
    contract = {"name": "Old", "world": {"n": 0}, "types": {"p": AGENT}, **patch}
    with pytest.raises(fg_env.ContractError) as caught:
        fg_env.parse(contract)
    assert fix in str(caught.value)


def test_scheduled_turns_and_timed_wakes_are_refused_by_check():
    contract = {"name": "Old", "types": {"p": AGENT}, "entities": {"p": {"type": "p"}},
                "actions": {"go": {"by": "p", "do": [{"wake": "$actor", "in": 2}]}},
                "stages": [{"name": "s", "turns": "scheduled"}]}
    errors = [str(i) for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert any("scheduled turns were removed" in e for e in errors)
    assert any("inside an `after` effect" in e for e in errors)
