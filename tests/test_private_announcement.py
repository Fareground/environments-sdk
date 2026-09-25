"""An announcement is sent to everyone: reading an agent's private property in it is a check error, and the engine
refuses it; a reveal is worked out in `do` first."""
import copy

import pytest

import fg_env


def contract():
    return {
        "name": "Customer announcements", "clock": {"rounds": 1},
        "types": {"customer": {"agent": True, "props": {
            "balance": {"default": 137, "private": True}, "score": 5}}},
        "entities": {"a": {"type": "customer"}, "b": {"type": "customer"}},
        "actions": {"report": {"by": "customer", "announce": "Balance: {$actor.balance}"}},
        "stages": [{"name": "decide", "actions": ["report"]}],
    }


def warnings(c):
    return [i for i in fg_env.check(c, rounds=0) if i.path == "actions.report.announce"
            and "reads private" in i.message]


def test_direct_disclosure_is_an_error_with_a_path_and_a_fix():
    issues = warnings(contract())
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "error"
    assert "$actor.balance" in issue.message
    assert "$shown" in str(issue)


@pytest.mark.parametrize("repair", ["private", "outcome", "public_field", "no_announcement"])
def test_safe_alternatives_do_not_warn(repair):
    c = contract()
    action = c["actions"]["report"]
    if repair == "private":
        action["private"] = True
    elif repair == "outcome":
        action["outcome"] = action.pop("announce")
    elif repair == "public_field":
        action["announce"] = "Score: {$actor.score}"
    else:
        action.pop("announce")
    assert not warnings(c)


def test_entity_parameter_disclosure_and_repeated_references():
    c = contract()
    action = c["actions"]["report"]
    action["params"] = {"target": {"type": "entity", "of": "customer"}}
    action["announce"] = "{$params.target.balance} / {$params.target.balance}"
    found = warnings(c)
    assert len(found) == 1
    assert found[0].message.count("$params.target.balance") == 1


def test_inherited_privacy_and_explicit_override():
    c = contract()
    c["types"]["preferred"] = {"extends": "customer", "props": {"balance": 200}}
    c["actions"]["report"]["by"] = "preferred"
    assert warnings(c)
    c["types"]["preferred"]["props"]["balance"] = {"default": 200, "private": False}
    assert not warnings(c)


def test_unknown_parameter_type_and_broken_template_still_report_errors_without_crashing():
    for template in ("{$params.target.balance}", "{$actor.balance +}"):
        c = contract()
        c["actions"]["report"].update(params={"target": {"type": "entity", "of": "missing"}}, announce=template)
        assert any(i.severity == "error" for i in fg_env.check(c, rounds=0))


def test_a_worked_out_reveal_is_announced_and_a_private_action_announces_nothing():
    for private in (False, True):
        c = copy.deepcopy(contract())
        c["actions"]["report"].update(private=private, do=["$shown = $actor.balance"], announce="Balance: {$shown}")
        env = fg_env.load(c)

        def play(wake):
            if wake.entity_id == "a":
                assert wake.call("report", {}).ok
            wake.end()

        result = env.run(play)
        assert result.ok, result.summary()
        lines, _ = env.information.news(env.world.entities["b"], 0)
        assert any("Balance: 137" in line for line in lines) == (not private)


def _fetched(**sections):
    return {"name": "Fetched", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "props": {"secret": {"default": 0, "private": True}, "coins": 10}}},
            "entities": {"a": {"type": "p", "props": {"secret": 7}}, "b": {"type": "p", "props": {"secret": 3}}},
            "actions": {"g": {"by": "p", "do": "$actor.coins += 1"}}, **sections}


def test_a_fetched_private_read_in_an_outcome_or_an_invariant_why_is_a_check_error():
    """An outcome is shown to its actor and an invariant's `why` to whoever broke it: another agent's private property
    fetched into either is refused at run time, so check says so as an error, as for views."""
    outcome = _fetched()
    outcome["actions"]["g"]["outcome"] = "B has {$entity(b).secret}"
    invariant = _fetched(invariants=[{"expr": "$entity(a).coins >= 0", "why": "b holds {$entity(b).secret}"}])
    for contract, path in ((outcome, "actions.g.outcome"), (invariant, "invariants[0].why")):
        found = [i for i in fg_env.check(contract, rounds=0) if i.path == path]
        assert [i.severity for i in found] == ["error"], (path, [str(i) for i in found])
        assert "$entity(…).secret" in found[0].message


def test_a_parameter_default_reading_another_agents_private_property_is_a_check_error():
    """A default fills in what the agent left out, and is shown in its tool: reading another agent's private property
    there would fail the run the first time the tool is offered, so check refuses it before (audit hands-on M2)."""
    contract = _fetched()
    contract["actions"]["g"]["params"] = {"n": {"type": "int", "default": "$entity(b).secret"}}
    found = [i for i in fg_env.check(contract, rounds=0) if i.path == "actions.g.params.n.default"]
    assert [i.severity for i in found] == ["error"]
    with pytest.raises(fg_env.ContractError):
        fg_env.load(contract)


def _defaulted(**action):
    return {"name": "Defaults", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "props": {"secret": {"default": 0, "private": True}, "pub": 0}}},
            "entities": {"a": {"type": "p", "props": {"secret": 42}}, "b": {"type": "p"}},
            "actions": {"cho": {"by": "p", "params": {"n": {"type": "int", "default": "$actor.secret", "min": 0,
                                                            "max": 100}},
                                "do": ["$actor.pub = 2"], **action}},
            "stages": [{"name": "s", "order": "$it.id"}], "outputs": {"p": "$entity(a).pub"}}


def test_an_argument_with_a_worked_out_default_is_never_announced():
    """A default is worked out as its actor sees the world, so what it fills in is the actor's to know (audit 11 H1):
    the announcement and the action's event leave it out, whether or not the agent passed it."""
    for args in ({}, {"n": 42}):
        seen = {}

        def play(wake, args=args, seen=seen):
            if wake.entity_id == "a":
                assert wake.call("cho", args).ok
            else:
                seen["update"] = wake.update
            wake.end()

        result = fg_env.run(_defaulted(), play, seed=1)
        assert "- a: cho." in seen["update"] and "42" not in seen["update"]
        assert [e["data"]["params"] for e in result.events if e["kind"] == "action"] == [{}]


def test_an_announcement_reading_an_argument_with_a_worked_out_default_is_a_check_error():
    contract = _defaulted(announce="{$actor.name} chose {$params.n}")
    found = [i for i in fg_env.check(contract, rounds=0) if i.path == "actions.cho.announce"]
    assert [i.severity for i in found] == ["error"] and "$params.n" in found[0].message
    with pytest.raises(fg_env.ContractError):
        fg_env.load(contract)
