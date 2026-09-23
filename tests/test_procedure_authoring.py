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


def shared_actions_contract(names=("north", "south")):
    return {
        "name": "Parallel approvals", "clock": {"rounds": 2},
        "types": {"manager": {"agent": True}}, "entities": {"manager": {"type": "manager"}},
        "actions": {"approve": {"by": "manager", "do": []}},
        "mechanisms": {name: {"kind": "flow", "mode": "procedure", "phases": {
            "review": {"stages": [{"actions": ["approve"]}],
                       "next": [{"to": "done", "all_did": "approve"}]}, "done": {},
        }} for name in names},
    }


def shared_warnings(c):
    return [i for i in warnings(c) if "same action" in i.message]


def test_shared_completion_is_explicit_without_silently_changing_global_semantics():
    c = shared_actions_contract()
    found = shared_warnings(c)
    assert {i.path for i in found} == {
        f"mechanisms.{name}.phases.review.next[0].all_did" for name in ("north", "south")}
    assert all("any stage" in i.message and "distinct action names" in i.fix for i in found)
    env = fg_env.load(c)

    def participant(wake):
        if wake.stage == "north_review":
            assert wake.call("approve", {}).ok
        wake.end()

    env.run(participant, rounds=1)
    assert env.props["north_phase"] == env.props["south_phase"] == "done"


def test_separate_action_names_keep_approvals_independent():
    c = shared_actions_contract()
    c["actions"] = {f"approve_{name}": {"by": "manager", "do": []} for name in ("north", "south")}
    for name, cfg in c["mechanisms"].items():
        phase = cfg["phases"]["review"]
        phase["stages"][0]["actions"] = [f"approve_{name}"]
        phase["next"][0]["all_did"] = f"approve_{name}"
    assert shared_warnings(c) == []
    env = fg_env.load(c)

    def participant(wake):
        if wake.stage == "north_review":
            assert wake.call("approve_north", {}).ok
        wake.end()

    env.run(participant, rounds=1)
    assert env.props["north_phase"] == "done"
    assert env.props["south_phase"] == "review"


def test_repeated_transitions_within_one_procedure_are_not_shared_completion():
    c = shared_actions_contract(("north",))
    c["mechanisms"]["north"]["phases"]["done"]["next"] = [{"to": "review", "all_did": "approve"}]
    assert shared_warnings(c) == []


def test_shared_completion_lists_other_procedures_once_in_stable_order():
    c = shared_actions_contract(("west", "south", "north"))
    c["mechanisms"]["south"]["phases"]["done"]["next"] = [{"to": "review", "all_did": "approve"}]
    found = [i for i in shared_warnings(c) if i.path.startswith("mechanisms.west.")]
    assert len(found) == 1
    assert "procedures north, south;" in found[0].message


def test_shared_completion_does_not_suppress_cohort_diagnostics():
    c = shared_actions_contract()
    c["types"]["manager"]["props"] = {"eligible": True}
    c["actions"]["approve"]["when"] = ["$actor.eligible"]
    found = warnings(c)
    assert len(shared_warnings(c)) == 2
    assert len([i for i in found if "every living entity" in i.message]) == 2
