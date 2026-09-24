"""World logic cannot be refused: a `fail` or an unfunded transfer in an event, a stage hook or a trigger is a contract
bug no agent can fix, so it fails the run at its path (and check's smoke play reports it) instead of being undone
silently while the run reports "completed"."""
import pytest

import fg_env

BANK = {
    "name": "Bank",
    "clock": {"rounds": 3},
    "world": {"cleared": 0},
    "types": {"p": {"agent": True, "props": {"cash": {"type": "int", "default": 3}}}},
    "entities": {"ann": {"type": "p"}, "bank": {"type": "p", "props": {"cash": 0}}},
    "actions": {"wait": {"by": "p", "do": []}},
    "outputs": {"cleared": "$world.cleared"},
}


def _with(**sections):
    return {**BANK, **sections}


def test_an_unfunded_transfer_in_an_event_fails_the_run_at_its_path():
    contract = _with(events=[{"phase": "end", "do": [
        "$world.cleared += 1", {"transfer": "cash", "from": "$entity(bank)", "to": "$entity(ann)", "amount": 100}]}])
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(contract, "idle", seed=1)
    result = failed.value.result
    assert result.status == "failed" and result.error.startswith("events[0].do: ")
    assert "bank has only 0 cash" in result.error and "guard it with an `if`" in result.error


def test_a_fail_in_a_stage_event_fails_the_run_and_check_reports_it():
    contract = _with(stages=[{"name": "s"}],
                     events=[{"on": "stage.s.end", "do": ["$world.cleared += 1",
                                                          {"if": "$round == 2", "then": [{"fail": "Too late."}]}]}])
    with pytest.raises(fg_env.RunError, match=r"events\[0\]\.do: Too late\."):
        fg_env.run(contract, "idle", seed=1)
    errors = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert any(i.path == "events[0].do" and "Too late." in i.message for i in errors)


def test_a_change_event_an_action_sets_off_that_is_refused_refuses_that_action_instead():
    contract = _with(events=[{"on": "change", "when": "$entity(ann).cash > 3", "do": [{"fail": "No."}]}],
                     actions={"earn": {"by": "p", "do": ["$actor.cash += 1"]}})
    told = []

    def earn(wake):
        told.append(wake.call("earn"))
        wake.end()

    env = fg_env.load(contract, seed=1)
    result = env.run({"ann": earn, "bank": "idle"}, rounds=1)
    assert result.status == "running" and not told[0].ok
    assert env.entity("ann")["props"]["cash"] == 3
    assert any(d["code"] == "action_rule_failed" and d["path"] == "events[0].do" for d in result.diagnostics)


def test_a_guarded_block_runs_normally():
    contract = _with(events=[{"phase": "end", "do": [{"if": "$entity(bank).cash >= 100", "then": [
        {"transfer": "cash", "from": "$entity(bank)", "to": "$entity(ann)", "amount": 100}]}]}])
    assert fg_env.run(contract, "idle", seed=1).status == "completed"
