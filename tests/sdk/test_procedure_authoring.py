"""Procedure completion diagnostics expose cohort scope without changing runtime semantics."""
import pytest

import fg_env


def contract(conditions):
    return {
        "name": "Cohort approvals", "clock": {"rounds": 2},
        "types": {"manager": {"agent": True, "props": {"north": False, "approved": False}}},
        "entities": {"a": {"type": "manager", "props": {"north": True}}, "b": {"type": "manager"}},
        "actions": {"approve": {"by": "manager", "when": conditions, "params": {"yes": "bool"},
                                 "do": ["$actor.approved = true"]}},
        "mechanisms": {"review": {"kind": "flow", "mode": "procedure", "phases": {
            "approval": {"stages": [{"actions": ["approve"]}],
                         "next": [{"to": "done", "all_did": "approve"}]},
            "done": {},
        }}},
    }


def warnings(c):
    return [i for i in fg_env.check(c, rounds=0) if i.path.endswith(".all_did") and i.severity == "warning"]


def test_actor_conditions_explain_completion_scope_once_at_the_transition():
    c = contract(["$actor.north", "not $actor.approved"])
    found = warnings(c)
    assert len(found) == 1
    assert found[0].path == "mechanisms.review.phases.approval.next[0].all_did"
    assert "every living entity" in found[0].message
    assert "filtered completion" in found[0].fix
    env = fg_env.load(c)

    def participant(wake):
        if wake.entity_id == "a" and wake.round == 1:
            assert wake.call("approve", {"yes": True}).ok
        wake.end()

    env.run(participant)
    assert env.props["review_phase"] == "approval"  # A warning never silently changes who must act.


@pytest.mark.parametrize("conditions", [[], ["$round >= 1"], ["$params.yes"],
                                         ["$params.yes or $actor.north"]])
def test_unconditional_global_and_parameter_conditions_do_not_add_cohort_warnings(conditions):
    assert warnings(contract(conditions)) == []


def test_explicit_filtered_completion_is_accepted_and_advances():
    c = contract(["$actor.north"])
    c["mechanisms"]["review"]["phases"]["approval"]["next"] = [
        {"to": "done", "when": "$all($filter(manager, $it.north), $it.approved)"}]
    assert warnings(c) == []
    env = fg_env.load(c)

    def participant(wake):
        if wake.entity_id == "a" and wake.round == 1:
            assert wake.call("approve", {"yes": True}).ok
        wake.end()

    env.run(participant)
    assert env.props["review_phase"] == "done"


def test_invalid_action_condition_still_reports_its_own_error():
    issues = list(fg_env.check(contract(["$actor."]), rounds=0))
    assert any(i.severity == "error" and i.path.startswith("actions.approve.when") for i in issues)
