"""A public template can bypass inspect privacy; flag that choice without changing execution."""
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
    return [i for i in fg_env.check(c, rounds=0) if "public announcement references private" in i.message]


def test_direct_disclosure_has_a_path_and_actionable_nonblocking_warning():
    issues = warnings(contract())
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "warning"
    assert issue.path == "actions.report.announce"
    assert "$actor.balance" in issue.message
    assert "outcome" in str(issue) and "private: true" in str(issue)


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


def test_public_and_private_announcements_have_the_warned_runtime_behavior():
    for private in (False, True):
        c = copy.deepcopy(contract())
        c["actions"]["report"]["private"] = private
        env = fg_env.load(c)

        def play(wake):
            if wake.entity_id == "a":
                assert wake.call("report", {}).ok
            wake.end()

        result = env.run(play)
        assert result.ok, result.summary()
        lines, _ = env.perception.news(env.world.entities["b"], 0)
        assert any("Balance: 137" in line for line in lines) == (not private)
