"""Mechanisms: the extension spine and the voting building blocks."""
import json
import random
import tracemalloc

import pytest

import fg_env
from fg_env.errors import ContractError
from fg_env.mechanisms.voting import tally


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



def test_a_ballot_short_of_quorum_still_shows_its_counts_but_decides_nothing():
    short = tally("plurality", {"a": "x", "b": "x", "c": "y"}, ["x", "y"], eligible=10, quorum=0.5)
    assert short["reason"] == "no quorum" and short["winner"] is None and not short["decided"]
    assert short["counts"] == {"x": 2, "y": 1} and short["turnout"] == pytest.approx(0.3)
    tied = tally("plurality", {"a": "x", "b": "y"}, ["x", "y"], eligible=10,
                 quorum=0.5)  # no seed needed: no tie is drawn
    assert tied["counts"] == {"x": 1, "y": 1} and tied["winner"] is None


def test_a_ranked_count_without_tie_breaks_eliminates_every_option_tied_for_last():
    # y and z tie for last with 2 each: ties none drops both at once, so their voters' next choices decide
    ballots = [["x"]] * 4 + [["y", "w"]] * 2 + [["z", "w"]] * 2 + [["w"]] * 3
    irv = tally("ranked", ballots, ["x", "y", "z", "w"], ties="none")
    assert irv["winner"] == "w" and [sorted(r["counts"]) for r in irv["rounds"]] == [["w", "x", "y", "z"], ["w", "x"]]


def test_a_ranked_count_breaks_ties_first_the_way_every_other_method_does_and_says_a_tie_decided_it():
    # b and c tie for last: ties first keeps the first-declared option (b), as plurality's ties first makes it win
    ballots = [["a", "c", "b"]] * 3 + [["b", "c", "a"]] * 2 + [["c", "b", "a"]] * 2
    irv = tally("ranked", ballots, ["a", "b", "c"], ties="first")
    assert irv["winner"] == "b" and irv["tie"] and irv["tied"] == ["b", "c"]
    assert tally("plurality", {"x": "b", "y": "c"}, ["a", "b", "c"], ties="first")["winner"] == "b"
    assert not tally("ranked", [["a"], ["a"], ["b"]], ["a", "b"])["tie"]


@pytest.mark.parametrize("method", ["ranked", "plurality"])
def test_the_ranking_always_starts_with_the_winner_after_a_random_tie_break(method):
    ballots = [["a"], ["b"]] if method == "ranked" else ["a", "b"]
    for seed in range(12):
        result = tally(method, ballots, ["a", "b"], rng=random.Random(seed))
        assert result["tie"] and result["ranking"][0] == result["winner"]


def test_passed_means_the_motion_listed_first_carried_and_decided_means_a_winner():
    lost = tally("majority", {"a": "yes", "b": "no", "c": "no"}, ["yes", "no"])
    assert lost["winner"] == "no" and lost["decided"] and not lost["passed"]
    carried = tally("majority", {"a": "yes", "b": "yes", "c": "no"}, ["yes", "no"])
    assert carried["winner"] == "yes" and carried["decided"] and carried["passed"]
    deadlock = tally("majority", {"a": "yes", "b": "no"}, ["yes", "no"], ties="none")
    assert deadlock["winner"] is None and not deadlock["decided"] and not deadlock["passed"]


def test_a_tie_at_the_top_never_meets_a_threshold_unless_ties_first_breaks_it():
    even = {"a": "yes", "b": "no"}
    for seed in range(8):
        split = tally("majority", even, ["yes", "no"], threshold=0.5, rng=random.Random(seed))
        assert split["tie"] and split["winner"] is None and not split["passed"] and "tied" in split["reason"]
    three = tally("supermajority", {"a": "x", "b": "x", "c": "y", "d": "y", "e": "z"}, ["x", "y", "z"], threshold=0.4,
                  rng=random.Random(1))
    assert three["winner"] is None and three["tied"] == ["x", "y"]
    casting = tally("majority", even, ["yes", "no"], threshold=0.5, ties="first")
    assert casting["winner"] == "yes" and casting["passed"]


def test_a_ballot_for_an_option_not_on_the_ballot_is_refused():
    with pytest.raises(ValueError, match="'maybe' is not on the ballot"):
        tally("plurality", {"a": "yes", "b": "maybe"}, ["yes", "no"])
    with pytest.raises(ValueError, match="'w' is not on the ballot"):
        tally("ranked", [["x", "w"]], ["x", "y"])


def test_weighted_votes_a_members_threshold_and_a_veto():
    shares = tally("majority", {"a": "yes", "b": "no", "c": "no"}, ["yes", "no"], weights={"a": 60, "b": 25, "c": 15})
    assert shares["passed"] and shares["share"] == pytest.approx(0.6) and shares["votes"] == 100
    # cloture: three fifths of all members, not of those voting
    members = {"a": "yes", "b": "yes", "c": "no"}
    assert tally("supermajority", members, ["yes", "no"], threshold=0.6)["passed"]
    short = tally("supermajority", members, ["yes", "no"], threshold=0.6, base=5)
    assert not short["passed"] and short["share"] == pytest.approx(0.4)
    assert tally("supermajority", {**members, "d": "yes"}, ["yes", "no"], threshold=0.6, base=5)["passed"]
    # a veto-holder's vote against defeats the motion; its abstention does not
    council = {"p1": "no", "p2": "yes", "e1": "yes", "e2": "yes"}
    vetoed = tally("majority", council, ["yes", "no"], vetoers=["p1", "p2"])
    assert not vetoed["passed"] and vetoed["decided"] and vetoed["winner"] == "no" and vetoed["vetoed"] == ["p1"]
    assert tally("majority", {**council, "p1": "abstain"}, ["yes", "no"], vetoers=["p1", "p2"])["passed"]


COUNCIL = {
    "name": "Budget council",
    "clock": {"rounds": 2},
    "inputs": {"bar": {"type": "number", "default": 0.5}},
    "types": {"member": {"agent": True, "props": {"mood": 0}}},
    "population": [{"type": "member", "count": 5}],
    "mechanisms": {"budget": {"kind": "decision", "mode": "ballot", "who": "member", "options": ["approve", "reject"],
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
    members = env.world.entities_of("member")
    assert all(member.properties["budget_ballot"] is None for member in members)  # a fresh ballot for the next vote
    assert result.outputs["decision"] == "approve"
    announced = [e for e in result.events if e["kind"] == "budget"]
    assert announced and "approve wins" in announced[0]["text"]
    assert not any(e["kind"] == "action" and e.get("to") is None for e in result.events)  # ballots are secret
    env.run(watching, rounds=1)
    assert "Adopt the budget?: approve wins" in updates["member_5"][-1]


def test_a_ballot_costs_the_same_however_many_have_voted():
    def peak(voters):
        contract = {"name": "b", "clock": {"rounds": 1}, "types": {"v": {"agent": True}},
                    "entities": {f"v{i}": {"type": "v"} for i in range(voters)},
                    "mechanisms": {"e": {"kind": "decision", "mode": "ballot", "who": "v", "options": ["a", "b"]}}}
        env = fg_env.load(contract, seed=1)
        tracemalloc.start()
        try:
            env.run("random")
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    # Each ballot is its voter's own property: four times the voters take about four times the memory, not sixteen
    # (a shared map of ballots was copied on every vote).
    assert peak(800) < 6 * peak(200)


def test_quorum_and_one_ballot_per_voter():
    env = fg_env.load(COUNCIL, seed=1)
    env.run(_voter({"member_1": "approve", "member_2": "reject"}), rounds=1)
    assert env.props["budget_result"]["reason"] == "no quorum"
    seen = []

    def double(wake):
        seen.append(wake.call("budget_vote", {"choice": "approve"}).ok)
        wake.end()

    fg_env.load({**COUNCIL, "stages": [{"name": "talk", "turns": "sequential", "max_actions": 2}],
                 "mechanisms": {"budget": {**COUNCIL["mechanisms"]["budget"], "stage": "talk"}}},
                seed=1).run(double, rounds=1)
    assert True in seen


def test_a_declared_event_replaces_the_generated_one_of_its_name():
    market = {"name": "Market", "clock": {"rounds": 3}, "types": {"p": {"agent": True, "props": {"cash": 100}}},
              "entities": {"a": {"type": "p"}, "b": {"type": "p"}}, "world": {"closed": 0},
              "events": [{"name": "acme_close", "phase": "end", "at": 2, "do": ["$world.closed += 1"]}],
              "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "p", "start_price": 10}}}
    contract = fg_env.parse(market)
    assert [e.name for e in contract.events].count("acme_close") == 1
    env = fg_env.load(market, seed=1)
    env.run("idle")
    assert env.props["closed"] == 1


def test_authors_override_generated_parts_and_arms_patch_mechanism_config():
    custom = json.loads(json.dumps(COUNCIL))
    custom["actions"] = {"budget_vote": {"by": "member", "description": "Say aye.", "params": {},
                                         "do": ["$actor.budget_ballot = 'approve'"], "terminal": True}}
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

def _security_council(**config):
    council = {"kind": "decision", "mode": "ballot", "who": "member", "options": ["adopt", "reject"],
               "method": "supermajority", "threshold": 0.6, "threshold_of": "members", "veto": "$it.permanent",
               **config}
    return {"name": "Council", "clock": {"rounds": 1},
            "types": {"member": {"agent": True, "props": {"permanent": False, "shares": 1}}},
            "entities": {**{p: {"type": "member", "props": {"permanent": True}} for p in ("p1", "p2")},
                         **{e: {"type": "member"} for e in ("e1", "e2", "e3")}},
            "mechanisms": {"resolution": council}}


def _votes(choices):
    def participant(wake):
        choice = choices.get(wake.entity_id)
        if choice == "abstain":
            wake.call("resolution_abstain")
        elif choice:
            wake.call("resolution_vote", {"choice": choice})
        wake.end()
    return participant


def test_ballot_weights_votes_measures_the_threshold_over_members_and_honours_a_veto():
    def result(contract, choices):
        env = fg_env.load(contract, seed=1)
        assert env.run(_votes(choices)).status == "completed"
        return env.props["resolution_result"]

    yes = {"p1": "adopt", "p2": "adopt", "e1": "adopt", "e2": "reject"}
    assert result(_security_council(), yes)["passed"]
    vetoed = result(_security_council(), {**yes, "p1": "reject", "e3": "adopt"})
    assert not vetoed["passed"] and vetoed["vetoed"] == ["p1"]
    assert result(_security_council(), {**yes, "p1": "abstain", "e3": "adopt"})["passed"]
    assert not result(_security_council(), {"p1": "adopt", "p2": "adopt", "e1": "reject"})["passed"]  # 2 of 5 members
    shareholders = _security_council(method="majority", threshold=None, threshold_of="votes", veto=None,
                                     weight="$it.shares")
    shareholders["entities"]["e3"]["props"] = {"shares": 10}
    held = result(shareholders, {"p1": "adopt", "p2": "adopt", "e1": "adopt", "e2": "adopt", "e3": "reject"})
    assert held["winner"] == "reject" and held["counts"] == {"reject": 10, "adopt": 4}
    words = fg_env.load({**shareholders, "mechanisms": {"resolution": {**shareholders["mechanisms"]["resolution"],
                                                                     "weight": "$it.name"}}}, seed=1).run(_votes({}))
    assert words.status == "failed" and "a voter's weight must be a number ≥ 0, got 'p1'" in words.error


def test_an_expression_field_is_worked_out_as_one_with_or_without_a_dollar():
    """`veto: "false"` is false and `weight: "2"` is two; a bare property name is the forgotten `$it.` it looks like
    (audit 9 mech H2)."""
    yes = {"p1": "adopt", "p2": "adopt", "e1": "adopt", "e2": "reject"}
    env = fg_env.load(_security_council(veto="false", weight="2"), seed=1)
    env.run(_votes({**yes, "p1": "reject", "e3": "adopt"}))
    result = env.props["resolution_result"]
    assert result["passed"] and not result.get("vetoed") and result["counts"] == {"adopt": 6, "reject": 4}
    forgot = [i for i in _issues(_security_council(veto="permanent")) if i.path == "mechanisms.resolution.veto"]
    assert forgot and "`$it.permanent`" in forgot[0].fix


def test_a_voters_weight_or_veto_is_checked_against_the_voter_type_at_its_field():
    """Typos in `$it.<prop>` used to surface only in a play, at a generated event's path (audit 9 mech M1)."""
    for field, source in (("veto", "$it.permanet"), ("weight", "$it.sharez")):
        issues = [i for i in _issues(_security_council(**{field: source})) if i.severity == "error"]
        assert [i.path for i in issues] == [f"mechanisms.resolution.{field}"], issues
        assert "has no property" in issues[0].message


def test_a_veto_or_members_threshold_that_cannot_apply_is_refused():
    three = _issues(_security_council(options=["a", "b", "c"]))
    assert any(i.path == "mechanisms.resolution.veto" and "two options" in i.message for i in three)
    plurality = _issues(_security_council(method="plurality", threshold=None, veto=None))
    assert any(i.path == "mechanisms.resolution.threshold_of" for i in plurality)


def test_mechanism_runs_snapshot_and_resume_identically():
    straight = fg_env.load(COUNCIL, seed=5).run().to_dict()
    env = fg_env.load(COUNCIL, seed=5)
    env.run(rounds=1)
    restored = fg_env.Env.restore(COUNCIL, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def _budget(**config):
    return {**COUNCIL, "mechanisms": {"budget": config}}


def _issues(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


def test_mechanism_config_errors_say_what_to_fix():
    with pytest.raises(ContractError, match="did you mean 'ballot'"):
        fg_env.parse(_budget(kind="decision", mode="balot", who="member"))
    assert any("did you mean 'decision'" in (i.fix or "") for i in _issues(_budget(kind="decisions", mode="ballot")))
    missing_mode = _issues(_budget(kind="decision", who="member"))
    assert any("needs a `mode`" in i.message and "ballot, deliberation" in (i.fix or "") for i in missing_mode)
    wrong = _issues(_budget(kind="decision", mode="ballot", who="citizen", options=["a"]))
    assert any("who 'citizen' is not a declared type" in i.message for i in wrong)


def test_a_mode_written_as_the_kind_names_its_family():
    old = next(i for i in _issues(_budget(kind="ballot", voters="member", options=["a"])) if i.path
               == "mechanisms.budget.kind")
    assert old.message == "'ballot' is a mode of kind 'decision'"
    assert '"kind": "decision", "mode": "ballot"' in old.fix


def test_a_field_of_another_mode_or_a_typo_names_the_mode_and_its_fields():
    typo = _issues(_budget(kind="decision", mode="ballot", who="member", options=["a"], quorom=0.5))[0]
    assert typo.path == "mechanisms.budget.quorom"
    assert typo.message == "`quorom` is not a field of `decision` mode `ballot`"
    assert typo.fix.startswith("did you mean 'quorum'?") and "takes: who, options, method" in typo.fix
    foreign = _issues(_budget(kind="decision", mode="ballot", who="member", options=["a"], chair="member"))
    assert [i.message for i in foreign] == ["`chair` is not a field of `decision` mode `ballot`"]
    actor = _issues(_budget(kind="decision", mode="ballot", options=["a"], voters="member"))
    assert any(i.path == "mechanisms.budget.voters" and i.fix.startswith("did you mean 'who'?") for i in actor)


def test_check_warns_when_an_authored_action_replaces_a_generated_one_without_its_effect():
    expanded = fg_env.expand(COUNCIL, mechanisms=True)["actions"]["budget_vote"]
    silent = {**COUNCIL, "actions": {"budget_vote": {**expanded, "do": []}}}
    warned = [i for i in fg_env.check(silent) if i.severity == "warning" and i.path == "actions.budget_vote"]
    assert len(warned) == 1 and "budget" in warned[0].message and "fg-env expand" in warned[0].fix
    guarded = {**COUNCIL,
               "actions": {"budget_vote": {**expanded, "when": [{"expr": "$actor.mood >= 0", "why": "Too upset."}]}}}
    assert not [i for i in fg_env.check(guarded) if i.path == "actions.budget_vote"]


def test_family_ops_are_checked_against_the_action_they_name():
    def op_issues(*effects):
        return [(i.path, i.message, i.fix) for i in _issues({**COUNCIL, "events": [{"do": list(effects)}]})]

    assert any("'loudly' is not part of `decision.tally`" in m for _, m, _ in op_issues(
        {"decision": "budget", "action": "tally", "loudly": True}))
    path, message, fix = op_issues({"decision": "budget", "action": "count"})[0]
    assert path.endswith(".action") and message == "'count' is not an action of budget (decision ballot)"
    assert fix == "actions: tally"
    path, message, _ = op_issues({"decision": "budget"})[0]
    assert path.endswith(".action") and message == "needs an `action`"
    path, _, fix = op_issues({"decision": "budgett", "action": "tally"})[0]
    assert path.endswith(".decision") and fix == "did you mean 'budget'?"
    _, _, fix = op_issues({"tally": "budget"})[0]
    assert fix.startswith('`tally` is an action of the `decision` op: {"decision": "<mechanism>", "action": "tally"')


def test_guide_documents_mechanisms_and_native_ops():
    text = fg_env.guide("mechanisms")
    assert "| `decision` | ballot, deliberation, procedure |" in text
    page = fg_env.guide("decision.ballot")
    assert page.startswith("### `decision.ballot`") and "`quorum`" in page and "- `tally`" in page
    family = fg_env.guide("decision")
    assert "- `deliberation`:" in family and "### `decision.deliberation`" not in family  # modes are their own parts
    deliberation = fg_env.guide("decision.deliberation")
    assert "- `speak`" in deliberation and "- `open`" not in deliberation
    assert '- `decision`: {"decision": "<decision mechanism>", "action": ...}' in fg_env.guide("effects")
    assert "$decisions(" in family and "$decisions(" in fg_env.guide("functions.decision")
    assert "$tally_votes(" in fg_env.guide("functions.stats")  # counts any ballots: not only a mechanism's
    with pytest.raises(KeyError):
        fg_env.guide("decision.nope")


def test_a_def_shadows_a_built_in_function_of_the_same_name():
    contract = {**COUNCIL, "defs": {"median": {"args": ["x"], "expr": "$x * 10"}},
                "world": {"shown": 0},
                "stages": [{"name": "s", "turns": "sequential", "on_enter": ["$world.shown = $median(4)"]}]}
    issues = fg_env.check(contract)
    assert not [i for i in issues if i.severity == "error"], issues
    assert any("shadows the built-in $median" in i.message for i in issues)
    env = fg_env.load(contract, seed=1)
    env.run("idle", rounds=1)
    assert env.props["shown"] == 40


SHOP_LIST = {
    "name": "Basket",
    "clock": {"rounds": 1},
    "world": {"picked": {"type": "list", "default": []}},
    "types": {"shopper": {"agent": True}, "item": {"props": {"price": 1}}},
    "entities": {"s": {"type": "shopper"}, "apple": {"type": "item"}, "pear": {"type": "item"},
                 "fig": {"type": "item"}},
    "actions": {
        "rank": {"by": "shopper", "params": {"order": {"type": "list", "values": ["red", "green", "blue"],
                                                       "min_items": 2, "max_items": 3}},
                 "do": ["$world.picked = $params.order"], "terminal": True},
        "basket": {"by": "shopper", "params": {"items": {"type": "list", "of": "item", "max_items": 2}},
                   "do": ["$world.picked = $map($params.items, $it.id)"], "terminal": True},
        "numbers": {"by": "shopper", "params": {"xs": {"type": "list", "items": {"type": "int", "min": 0, "max": 9},
                                                       "unique": False}},
                    "do": ["$world.picked = $params.xs"], "terminal": True},
    },
    "stages": [{"name": "shop", "turns": "sequential"}],
}


def test_list_parameters_validate_every_item_and_render_as_arrays():
    env = fg_env.load(SHOP_LIST, seed=1)
    book, actor = env.actions, env.world.entities["s"]
    schema = env.information.tool(actor, "rank").input_schema["properties"]["order"]
    assert schema["type"] == "array" and schema["items"]["enum"] == ["red", "green", "blue"]
    assert schema["minItems"] == 2 and schema["maxItems"] == 3 and schema["uniqueItems"]
    assert book.validate(actor, "rank", {"order": ["red"]})[1] == "order needs at least 2 item(s), got 1"
    assert "more than once" in book.validate(actor, "rank", {"order": ["red", "red"]})[1]
    assert "item 2 must be one of" in book.validate(actor, "rank", {"order": ["red", "pink"]})[1]
    assert book.validate(actor, "rank", {"order": '["blue", "red"]'})[0]["order"] == ["blue", "red"]
    assert book.validate(actor, "rank", {"order": "green, blue"})[0]["order"] == ["green", "blue"]
    items = book.validate(actor, "basket", {"items": ["apple", "fig"]})[0]["items"]
    assert [i.id for i in items] == ["apple", "fig"]
    assert "allows at most 2" in book.validate(actor, "basket", {"items": ["apple", "fig", "pear"]})[1]
    assert book.validate(actor, "numbers", {"xs": [3, 3, 9]})[0]["xs"] == [3, 3, 9]
    assert "item 1 must be at most 9" in book.validate(actor, "numbers", {"xs": [12]})[1]
    assert fg_env.run(SHOP_LIST, seed=3).status != "failed"  # random agents fill arrays too


def test_list_parameter_contract_errors():
    bad = json.loads(json.dumps(SHOP_LIST))
    bad["actions"]["rank"]["params"]["order"].update(min_items=5, max_items=2)
    bad["actions"]["basket"]["params"]["items"] = {"type": "list", "items": {"type": "list"}}
    messages = [i.message for i in fg_env.check(bad) if i.severity == "error"]
    assert any("min_items (5) is more than max_items (2)" in m for m in messages)
    assert any("a list of lists is not supported" in m for m in messages)


def test_ranked_ballot_runs_instant_runoff():
    contract = {**COUNCIL, "mechanisms": {"budget": {"kind": "decision", "mode": "ballot", "who": "member",
                                                     "options": ["a", "b", "c"],
                                                     "method": "ranked", "question": "Pick a plan"}}}
    rankings = {"member_1": ["a"], "member_2": ["a"], "member_3": ["b"], "member_4": ["b"], "member_5": ["c", "b"]}

    def rank(wake):
        if "budget_vote" in {t.name for t in wake.tools}:
            wake.call("budget_vote", {"choices": rankings[wake.entity_id]})
        wake.end()

    env = fg_env.load(contract, seed=1)
    env.run(rank, rounds=1)
    result = env.props["budget_result"]
    assert result["winner"] == "b" and len(result["rounds"]) == 2


def _scratch_contract(family, mode_name, **sections):
    return {"name": "Scratch", "clock": {"rounds": 1}, "types": {"p": {"agent": True}},
            "entities": {"p": {"type": "p"}},
            "stages": [{"name": "s", "turns": "sequential"}], "mechanisms": {"n": {"kind": family, "mode": mode_name}},
            **sections}


def _go(env):
    def play(wake):
        assert wake.call("go").ok
        wake.end()

    return env.run(play)


def test_a_family_op_is_identified_by_its_own_key_even_with_core_op_named_fields():
    from family_fixtures import Nothing, scratch_family

    from fg_env.registry import family_action, mode

    with scratch_family("test_nudge"):
        mode("test_nudge", "counter", Nothing, "A counter.")(lambda name, config, contract: {})

        @family_action("test_nudge", ("counter",), "nudge", keys=("move",),
                       example='{"test_nudge": "n", "action": "nudge", "move": 2}')
        def _nudge(runner, effect, vars, where):
            world = runner.world
            world.set_world("count", world.props["count"] + runner.eval(effect["move"], vars))

        nudge = {"test_nudge": "n", "action": "nudge", "move": 2}
        contract = _scratch_contract("test_nudge", "counter", world={"count": 0},
                                     actions={"go": {"by": "p", "do": [nudge], "terminal": True}})
        assert not _issues(contract)
        env = fg_env.load(contract, seed=1)
        _go(env)
        assert env.props["count"] == 2
        both = {**contract, "actions": {"go": {"by": "p", "do": [{**nudge, "decision": "x"}], "terminal": True}}}
        assert any("names exactly one" in i.message for i in _issues(both))


def test_a_post_keeps_fields_named_like_family_ops_and_undeclared_mixes_are_ambiguous():
    from family_fixtures import Nothing, scratch_family

    from fg_env.registry import family_action, mode

    with scratch_family("test_stamp"):
        mode("test_stamp", "pad", Nothing, "A stamp pad.")(lambda name, config, contract: {})

        @family_action("test_stamp", ("pad",), "stamp", example='{"test_stamp": "n", "action": "stamp"}')
        def _stamp(runner, effect, vars, where):
            runner.world.set_world("stamped", 1)

        contract = _scratch_contract("test_stamp", "pad", world={"stamped": 0},
                                     records={"log": {"fields": {"test_stamp": "text"}, "notify": False}},
                                     actions={"go": {"by": "p", "do": [{"post": "log", "test_stamp": "hello"}],
                                                     "terminal": True}})
        assert not _issues(contract)
        env = fg_env.load(contract, seed=1)
        _go(env)
        assert [row["test_stamp"] for row in env.world.records_store["log"]] == ["hello"] and env.props["stamped"] == 0
        mixed = {**contract,
                 "actions": {"go": {"by": "p", "do": [{"test_stamp": "n", "action": "stamp", "move": "$actor"}],
                                    "terminal": True}}}
        assert any("names exactly one" in i.message for i in _issues(mixed))


def test_guide_renders_factory_defaults_of_mode_config():
    from family_fixtures import scratch_family
    from pydantic import BaseModel, Field

    from fg_env.registry import mode

    class WithFactory(BaseModel):
        tiebreak: list = Field(default_factory=list, description="Tie-breakers.")

    with scratch_family("test_factory"):
        mode("test_factory", "defaults", WithFactory, "Has a factory default.")(lambda name, config, contract: {})
        text = fg_env.guide("test_factory.defaults")
        assert "`tiebreak` (default [])" in text and "PydanticUndefined" not in text


def test_crashing_extensions_are_reported_against_their_use_never_raised_or_blamed_on_participants():
    from family_fixtures import Nothing, scratch_family

    from fg_env.registry import family_action, mode

    def crash(name, config, contract):
        raise KeyError("missing piece")

    def bad_check(checker, effect, path):
        raise ValueError("bad check")

    with scratch_family("test_crash"):
        mode("test_crash", "expand", Nothing, "Crashes while expanding.")(crash)
        mode("test_crash", "ops", Nothing, "Has crashing actions.")(lambda name, config, contract: {})
        family_action("test_crash", ("ops",), "checked", example="{}", check=bad_check)(
            lambda runner, effect, vars, where: None)

        @family_action("test_crash", ("ops",), "boom", example="{}")
        def _boom(runner, effect, vars, where):
            raise ValueError("kaboom")

        base = _scratch_contract("test_crash", "ops")
        issues = fg_env.check({**base, "mechanisms": {"m": {"kind": "test_crash", "mode": "expand"}}})
        assert any("failed to expand: KeyError" in i.message for i in issues)
        hooked = {**base, "mechanisms": {"vote": {"kind": "decision", "mode": "ballot", "who": "p", "options": ["a"],
                                                  "stage": "nowhere"}}}
        assert any("there is no stage 'nowhere'" in i.message for i in fg_env.check(hooked))
        checked = {**base,
                   "actions": {"go": {"by": "p", "do": [{"test_crash": "n", "action": "checked"}], "terminal": True}}}
        assert any("the `test_crash.checked` check failed: ValueError" in i.message for i in fg_env.check(checked))
        env = fg_env.load({**base, "actions": {"go": {"by": "p", "do": [{"test_crash": "n", "action": "boom"}],
                                                      "terminal": True}}}, seed=1)

        def play(wake):
            wake.call("go")
            wake.end()

        result = env.run(play)
        assert result.status == "failed" and "`test_crash.boom` failed: ValueError: kaboom" in result.error
        assert "participant" not in result.error


def test_check_and_preview_list_what_each_mechanism_generated(tmp_path, capsys):
    from fg_env.__main__ import main
    from fg_env.mechanisms import generated_summary

    contract = {"name": "Sale", "types": {"bidder": {"agent": True, "props": {"cash": 100}}},
                "entities": {"a": {"type": "bidder"}, "b": {"type": "bidder"}},
                "mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder",
                                        "item": "a painting"}}}
    lines = generated_summary(contract)
    assert len(lines) == 1 and lines[0].startswith("sale (market.auction): actions sale_bid")
    assert "stages sale" in lines[0] and "outputs" in lines[0]
    assert generated_summary({"name": "x", "types": {}}) == []
    path = tmp_path / "sale.json"
    path.write_text(json.dumps(contract))
    assert main(["check", str(path)]) == 0
    assert "sale (market.auction): actions sale_bid" in capsys.readouterr().out
    assert main(["preview", str(path), "a"]) == 0
    assert "mechanisms generated" in capsys.readouterr().out


def test_a_negative_ballot_weight_fails_the_count_naming_the_voter():
    contract = {"name": "Weights", "clock": {"rounds": 1},
                "types": {"member": {"agent": True, "props": {"shares": 1}}},
                "entities": {"a": {"type": "member", "props": {"shares": -5}}, "b": {"type": "member"}},
                "mechanisms": {"v": {"kind": "decision", "mode": "ballot", "who": "member", "options": ["yes", "no"],
                                     "weight": "$it.shares"}}}
    result = fg_env.load(contract, seed=1).run("idle")
    assert result.status == "failed" and "a voter's weight must be a number ≥ 0, got -5 for a" in result.error


def test_every_mechanism_example_the_reference_pages_show_is_a_valid_config():
    """The examples on the reference pages are what authors copy: each validates against its mode's config."""
    import fg_env.mechanisms  # noqa: F401  (registers them)
    from fg_env.registry import FAMILIES, config_data

    modes = [spec for family in FAMILIES.values() for spec in family.modes.values()]
    assert modes
    for spec in modes:
        spec.config.model_validate(config_data(spec.example))
