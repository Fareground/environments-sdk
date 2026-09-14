"""Mechanisms: the extension spine and the voting building blocks."""
import json
import random

import pytest

import fg_env
from fg_env.sdk.errors import ContractError
from fg_env.sdk.mechanisms.voting import tally


def test_tally_methods():
    assert tally("plurality", {"a": "x", "b": "y", "c": "x"}, ["x", "y"])["winner"] == "x"
    majority = tally("majority", {"a": "x", "b": "y"}, ["x", "y"], ties="none")
    assert majority["winner"] is None and not majority["passed"]
    super_ = tally("supermajority", {"a": "x", "b": "x", "c": "y"}, ["x", "y"])
    assert super_["winner"] == "x" and super_["share"] == pytest.approx(2 / 3)
    assert tally("supermajority", {"a": "x", "b": "y", "c": "y", "d": "x", "e": "x"}, ["x", "y"])["winner"] is None
    assert tally("approval", [["x", "y"], ["y"], ["z"]], ["x", "y", "z"])["winner"] == "y"
    assert tally("borda", [["x", "y", "z"], ["y", "x", "z"], ["y", "z", "x"]], ["x", "y", "z"])["winner"] == "y"
    score = tally("score", [{"x": 5, "y": 1}, {"x": 0, "y": 4}], ["x", "y"], ties="first")
    assert score["counts"] == {"x": 5, "y": 5} and score["tie"] and score["winner"] == "x"
    with pytest.raises(ValueError, match="randomness"):
        tally("score", [{"x": 1, "y": 1}], ["x", "y"])
    # instant runoff: z is eliminated and its voter's second choice decides
    irv = tally("ranked", [["x"], ["x"], ["y"], ["y"], ["z", "y"]], ["x", "y", "z"])
    assert irv["winner"] == "y" and len(irv["rounds"]) == 2
    condorcet = tally("condorcet", [["x", "y", "z"], ["y", "x", "z"], ["x", "z", "y"]], ["x", "y", "z"])
    assert condorcet["winner"] == "x" and condorcet["condorcet_winner"] == "x"
    quorum = tally("plurality", {"a": "x"}, ["x"], eligible=4, quorum=0.5)
    assert quorum["reason"] == "no quorum" and quorum["winner"] is None
    abstained = tally("plurality", {"a": "abstain", "b": "y"}, ["x", "y"])
    assert abstained["votes"] == 1 and abstained["cast"] == 2 and abstained["winner"] == "y"
    tied = [tally("plurality", {"a": "x", "b": "y"}, ["x", "y"], rng=random.Random(s))["winner"] for s in range(20)]
    assert set(tied) == {"x", "y"}


COUNCIL = {
    "name": "Budget council",
    "clock": {"rounds": 2},
    "inputs": {"bar": {"type": "number", "default": 0.5}},
    "types": {"member": {"agent": True, "props": {"mood": 0}}},
    "population": [{"type": "member", "count": 5}],
    "mechanisms": {"budget": {"kind": "ballot", "voters": "member", "options": ["approve", "reject"],
                              "method": "majority", "quorum": 0.6, "question": "Adopt the budget?"}},
    "outputs": {"decision": {"expr": "$world.budget_result.winner", "type": "text"}},
}


def _voter(choices):
    def participant(wake):
        tools = {t.name for t in wake.tools}
        choice = choices.get(wake.entity_id)
        if choice == "abstain" and "budget_abstain" in tools:
            wake.call("budget_abstain")
        elif choice and "budget_vote" in tools:
            wake.call("budget_vote", {"choice": choice})
        wake.end()
    return participant


def test_ballot_mechanism_runs_secret_votes_and_announces_the_result():
    env = fg_env.load(COUNCIL, seed=1)
    updates = {}

    def watching(wake):
        updates.setdefault(wake.entity_id, []).append(wake.update)
        _voter({"member_1": "approve", "member_2": "approve", "member_3": "approve", "member_4": "reject"})(wake)

    result = env.run(watching, rounds=1)
    assert result.status != "failed", result.error
    assert env.props["budget_result"]["winner"] == "approve"
    assert env.props["budget_ballots"] == {}  # a fresh ballot for the next vote
    assert result.outputs["decision"] == "approve"
    announced = [e for e in result.events if e["kind"] == "budget"]
    assert announced and "approve wins" in announced[0]["text"]
    assert not any(e["kind"] == "action" and e.get("to") is None for e in result.events)  # ballots are secret
    env.run(watching, rounds=1)
    assert "Adopt the budget?: approve wins" in updates["member_5"][-1]


def test_quorum_and_one_ballot_per_voter():
    env = fg_env.load(COUNCIL, seed=1)
    env.run(_voter({"member_1": "approve", "member_2": "reject"}), rounds=1)
    assert env.props["budget_result"]["reason"] == "no quorum"
    seen = []

    def double(wake):
        seen.append(wake.call("budget_vote", {"choice": "approve"}).ok)
        wake.end()

    fg_env.load({**COUNCIL, "stages": [{"name": "talk", "turns": "sequential", "max_actions": 2}],
                 "mechanisms": {"budget": {**COUNCIL["mechanisms"]["budget"], "stage": "talk"}}}, seed=1).run(double, rounds=1)
    assert True in seen


def test_authors_override_generated_parts_and_arms_patch_mechanism_config():
    custom = json.loads(json.dumps(COUNCIL))
    custom["actions"] = {"budget_vote": {"by": "member", "description": "Say aye.", "params": {},
                                         "do": ["$world.budget_ballots[$actor.id] = 'approve'"], "terminal": True}}
    custom["arms"] = {"strict": {"patch": {"mechanisms": {"budget": {"method": "supermajority", "threshold": 0.9}}}}}
    contract = fg_env.parse(custom)
    assert contract.actions["budget_vote"].description == "Say aye."
    assert "budget_abstain" in contract.actions
    env = fg_env.load(custom, seed=2, arm="strict")
    assert env.contract.mechanisms["budget"]["threshold"] == 0.9

    def ayes(wake):
        if "budget_vote" in {t.name for t in wake.tools} and wake.entity_id != "member_5":
            wake.call("budget_vote", {})
        wake.end()

    env.run(ayes, rounds=1)
    result = env.props["budget_result"]
    assert result["method"] == "supermajority" and result["share"] == 1 and result["winner"] == "approve"

def test_mechanism_runs_snapshot_and_resume_identically():
    straight = fg_env.load(COUNCIL, seed=5).run().to_dict()
    env = fg_env.load(COUNCIL, seed=5)
    env.run(rounds=1)
    restored = fg_env.Env.restore(COUNCIL, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_mechanism_config_errors_say_what_to_fix():
    bad = {**COUNCIL, "mechanisms": {"budget": {"kind": "balot", "voters": "member"}}}
    with pytest.raises(ContractError, match="did you mean 'ballot'"):
        fg_env.parse(bad)
    wrong = {**COUNCIL, "mechanisms": {"budget": {"kind": "ballot", "voters": "citizen", "options": ["a"]}}}
    issues = fg_env.check(wrong)
    assert any("voters 'citizen' is not a declared type" in i.message for i in issues)
    extra = {**COUNCIL, "mechanisms": {"budget": {"kind": "ballot", "voters": "member", "options": ["a"], "colour": 1}}}
    assert any(i.path == "mechanisms.budget.colour" for i in fg_env.check(extra))
    op = {**COUNCIL, "events": [{"do": [{"tally": "budget", "loudly": True}]}]}
    assert any("'loudly' is not part of `tally`" in i.message for i in fg_env.check(op))


def test_guide_documents_mechanisms_and_native_ops():
    text = fg_env.guide("mechanisms")
    assert "### `ballot`" in text and "`quorum`" in text
    assert '`tally`' in fg_env.guide("effects")
    assert "$tally_votes(" in fg_env.guide()


def test_a_def_shadows_a_built_in_function_of_the_same_name():
    contract = {**COUNCIL, "defs": {"median": {"args": ["x"], "expr": "$x * 10"}},
                "world": {"shown": 0}, "stages": [{"name": "s", "turns": "sequential", "on_enter": ["$world.shown = $median(4)"]}]}
    issues = fg_env.check(contract)
    assert not [i for i in issues if i.severity == "error"], issues
    assert any("shadows the built-in $median" in i.message for i in issues)
    env = fg_env.load(contract, seed=1)
    env.run("idle", rounds=1)
    assert env.props["shown"] == 40
