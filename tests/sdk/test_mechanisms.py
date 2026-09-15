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


def test_an_old_kind_name_says_the_new_kind_and_mode():
    old = next(i for i in _issues(_budget(kind="ballot", voters="member", options=["a"])) if i.path == "mechanisms.budget.kind")
    assert old.message == "'ballot' is now kind 'decision' with mode 'ballot'"
    assert '"kind": "decision", "mode": "ballot"' in old.fix


def test_a_field_of_another_mode_or_a_typo_names_the_mode_and_its_fields():
    typo = _issues(_budget(kind="decision", mode="ballot", who="member", options=["a"], quorom=0.5))[0]
    assert typo.path == "mechanisms.budget.quorom"
    assert typo.message == "`quorom` is not a field of `decision` mode `ballot`"
    assert typo.fix.startswith("did you mean 'quorum'?") and "takes: who, options, method" in typo.fix
    foreign = _issues(_budget(kind="decision", mode="ballot", who="member", options=["a"], chair="member"))
    assert [i.message for i in foreign] == ["`chair` is not a field of `decision` mode `ballot`"]


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
    assert fix.startswith('`tally` is now the `decision` op: {"decision": "<mechanism>", "action": "tally"')


def test_tools_one_offers_a_ballot_as_a_single_tool_and_auto_keeps_different_shapes_apart():
    offered = []

    def vote(wake):
        tools = {t.name: t for t in wake.tools}
        offered.append(tools)
        if "budget" in tools:
            assert wake.call("budget", {"action": "vote", "choice": "approve"}).ok
        wake.end()

    env = fg_env.load(_budget(**COUNCIL["mechanisms"]["budget"], tools="one"), seed=1)
    env.run(vote, rounds=1)
    assert "budget_vote" not in offered[0]
    assert offered[0]["budget"].input_schema["properties"]["action"]["enum"] == ["vote", "abstain"]
    assert env.props["budget_result"]["winner"] == "approve"
    auto = fg_env.parse(_budget(**COUNCIL["mechanisms"]["budget"], tools="auto"))
    assert auto.actions["budget_vote"].tool is None and auto.actions["budget_abstain"].tool is None


def test_guide_documents_mechanisms_and_native_ops():
    text = fg_env.guide("mechanisms")
    assert "| `decision` | ballot, deliberation |" in text
    page = fg_env.guide("decision.ballot")
    assert page.startswith("### `decision.ballot`") and "`quorum`" in page and "- `tally`" in page
    family = fg_env.guide("decision")
    assert "### `decision.deliberation`" in family and "- `speak`" in family and "- `open`" not in family
    assert '- `decision`: {"decision": "<decision mechanism>", "action": ...}' in fg_env.guide("effects")
    assert "$tally_votes(" in fg_env.guide()
    with pytest.raises(KeyError):
        fg_env.guide("decision.nope")


def test_a_def_shadows_a_built_in_function_of_the_same_name():
    contract = {**COUNCIL, "defs": {"median": {"args": ["x"], "expr": "$x * 10"}},
                "world": {"shown": 0}, "stages": [{"name": "s", "turns": "sequential", "on_enter": ["$world.shown = $median(4)"]}]}
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
    "entities": {"s": {"type": "shopper"}, "apple": {"type": "item"}, "pear": {"type": "item"}, "fig": {"type": "item"}},
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
    schema = book.tool(actor, "rank").input_schema["properties"]["order"]
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


def test_a_native_op_is_identified_by_its_own_key_even_with_core_op_named_fields():
    from fg_env.sdk.registry import OPS, effect_op

    name = "test_nudge_counter"

    @effect_op(name, keys=("move",), literal=(name,), example='{"test_nudge_counter": "n", "move": 2}')
    def _nudge(runner, effect, vars, where):
        world = runner.world
        world.set_world(effect[name], world.props[effect[name]] + runner.eval(effect["move"], vars))

    try:
        contract = {"name": "Nudge", "clock": {"rounds": 1}, "world": {"n": 0},
                    "types": {"p": {"agent": True}}, "entities": {"p": {"type": "p"}},
                    "actions": {"go": {"by": "p", "do": [{name: "n", "move": 2}], "terminal": True}},
                    "stages": [{"name": "s", "turns": "sequential"}]}
        assert not [i for i in fg_env.check(contract) if i.severity == "error"]
        env = fg_env.load(contract, seed=1)

        def play(wake):
            assert wake.call("go").ok
            wake.end()

        env.run(play)
        assert env.props["n"] == 2
        both = {**contract, "actions": {"go": {"by": "p", "do": [{name: "n", "board_move": "e2-e4"}],
                                               "terminal": True}}}
        assert any("names exactly one" in i.message for i in fg_env.check(both) if i.severity == "error")
    finally:
        OPS.pop(name, None)


def test_a_post_keeps_fields_named_like_native_ops_and_undeclared_mixes_are_ambiguous():
    from fg_env.sdk.registry import OPS, effect_op

    name = "test_stamp"

    @effect_op(name, keys=(), literal=(name,), example='{"test_stamp": "n"}')
    def _stamp(runner, effect, vars, where):
        runner.world.set_world(effect[name], 1)

    try:
        contract = {"name": "Post", "clock": {"rounds": 1}, "world": {"n": 0},
                    "types": {"p": {"agent": True}}, "entities": {"p": {"type": "p"}},
                    "records": {"log": {"fields": {name: "text"}, "notify": False}},
                    "actions": {"go": {"by": "p", "do": [{"post": "log", name: "hello"}], "terminal": True}},
                    "stages": [{"name": "s", "turns": "sequential"}]}
        assert not [i for i in fg_env.check(contract) if i.severity == "error"]
        env = fg_env.load(contract, seed=1)

        def play(wake):
            assert wake.call("go").ok
            wake.end()

        env.run(play)
        assert [row[name] for row in env.world.records_store["log"]] == ["hello"] and env.props["n"] == 0
        mixed = {**contract, "actions": {"go": {"by": "p", "do": [{name: "n", "move": "$actor"}], "terminal": True}}}
        assert any("names exactly one" in i.message for i in fg_env.check(mixed) if i.severity == "error")
    finally:
        OPS.pop(name, None)


def test_guide_renders_factory_defaults_of_mechanism_config():
    from pydantic import BaseModel, Field

    from fg_env.sdk.registry import MECHANISMS, mechanism

    class WithFactory(BaseModel):
        tiebreak: list = Field(default_factory=list, description="Tie-breakers.")

    kind = "test_factory_defaults"

    @mechanism(kind, WithFactory, "Has a factory default.")
    def _expand(name, config, contract):
        return {}

    try:
        text = fg_env.guide("mechanisms")
        assert "`tiebreak` (default [])" in text and "PydanticUndefined" not in text
    finally:
        MECHANISMS.pop(kind, None)


def test_crashing_extensions_are_reported_against_their_use_never_raised_or_blamed_on_participants():
    from pydantic import BaseModel

    from fg_env.sdk.registry import MECHANISMS, OPS, effect_op, mechanism

    class Empty(BaseModel):
        pass

    kind, op_check, op_run = "test_crashing_expand", "test_crashing_check", "test_crashing_run"

    @mechanism(kind, Empty, "Crashes while expanding.")
    def _expand(name, config, contract):
        raise KeyError("missing piece")

    def bad_check(checker, effect, path):
        raise ValueError("bad check")

    @effect_op(op_check, keys=(), literal=(op_check,), example="{}", check=bad_check)
    def _checked(runner, effect, vars, where):
        return None

    @effect_op(op_run, keys=(), literal=(op_run,), example="{}")
    def _boom(runner, effect, vars, where):
        raise ValueError("kaboom")

    try:
        base = {"name": "Crash", "clock": {"rounds": 1}, "types": {"p": {"agent": True}},
                "entities": {"p": {"type": "p"}}, "stages": [{"name": "s", "turns": "sequential"}]}
        issues = fg_env.check({**base, "mechanisms": {"m": {"kind": kind}}})
        assert any("failed to expand: KeyError" in i.message for i in issues)
        hooked = {**base, "mechanisms": {"vote": {"kind": "decision", "mode": "ballot", "who": "p", "options": ["a"],
                                                  "stage": "nowhere"}}}
        assert any("there is no stage 'nowhere'" in i.message for i in fg_env.check(hooked))
        issues = fg_env.check({**base, "actions": {"go": {"by": "p", "do": [{op_check: "x"}], "terminal": True}}})
        assert any("check failed: ValueError" in i.message for i in issues)
        env = fg_env.load({**base, "actions": {"go": {"by": "p", "do": [{op_run: "x"}], "terminal": True}}}, seed=1)

        def play(wake):
            wake.call("go")
            wake.end()

        result = env.run(play)
        assert result.status == "failed" and "`test_crashing_run` failed: ValueError: kaboom" in result.error
        assert "participant" not in result.error
    finally:
        MECHANISMS.pop(kind, None)
        OPS.pop(op_check, None)
        OPS.pop(op_run, None)
