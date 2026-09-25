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


def test_what_an_action_schedules_for_later_stays_that_actions_and_is_refused_not_fatal():
    """An action's `after` effects are still the action's: a refusal in them undoes that block alone, its agent is
    told, and the run's diagnostics count it against the action; the run goes on."""
    contract = _with(stages=[{"name": "play", "max_actions": 2}], actions={
        "pledge": {"by": "p", "do": [{"after": 1, "do": [
            "$world.cleared += 1", {"transfer": "cash", "from": "$actor", "to": "$entity(bank)", "amount": 2}]}]},
        "spend": {"by": "p", "do": ["$actor.cash = 0"]}})
    updates = []

    def ann(wake):
        updates.append(wake.update)
        if wake.round == 1:
            assert wake.call("pledge").ok and wake.call("spend").ok
        wake.end()

    env = fg_env.load(contract, seed=1)
    result = env.run({"ann": ann, "bank": "idle"})
    assert result.status == "completed", result.error
    assert result.outputs["cleared"] == 0  # the block was undone whole
    assert any("pledge" in update and "did not happen" in update and "ann has only 0 cash" in update
               for update in updates)
    assert env.state.diagnosis.actions["pledge"]["refused"] == 1


def test_a_fail_in_a_change_event_an_action_set_off_tells_its_agent_the_fail_text():
    """A `change` event set off by an agent's action is that action's: its `fail` refuses the action with the fail
    text, rendered for the agent, as a `fail` in a create event does (audit 11 L2)."""
    contract = {"name": "Flag", "clock": {"rounds": 1}, "world": {"flag": 0},
                "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
                "actions": {"set": {"by": "p", "do": ["$world.flag = 1"]}},
                "events": [{"on": "change", "when": "$world.flag == 1", "do": [{"fail": "The flag stays down."}]}]}
    replies = []

    def play(wake):
        replies.append(wake.call("set", {}).text)
        wake.end()

    result = fg_env.run(contract, play, seed=1)
    assert result.status == "completed", result.error
    assert replies == ["Your set was not done: The flag stays down. Nothing changed. Try other arguments or another "
                       "action."]
