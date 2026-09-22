"""Mechanisms compose: several of one kind share a contract, clashing names are refused, nothing ends the run unasked."""
import fg_env
from fg_env.sdk.expr import compile_expr
from fg_env.sdk.mechanisms import generated_summary


def errors(contract):
    return [str(i) for i in fg_env.check(contract) if i.severity == "error"]


def issues(contract):
    return [str(i) for i in fg_env.check(contract)]


def ev(env, source, **vars):
    return compile_expr(source)(env.world.scope(**vars))


def do(env, actor, effects):
    return env._atomic(effects, {"actor": env.world.entities[actor]} if actor else {}, "test")


BASE = {"name": "Composed", "clock": {"rounds": 3},
        "types": {"member": {"agent": True}, "committee_member": {"extends": "member"}},
        "population": [{"type": "member", "count": 3, "id": "m{$i}"}, {"type": "committee_member", "count": 2, "id": "c{$i}"}]}


def _with(**mechanisms):
    return {**BASE, "mechanisms": mechanisms}


BICAMERAL = _with(
    committee={"kind": "decision", "mode": "deliberation", "who": "committee_member", "question": "Report the bill?"},
    floor={"kind": "decision", "mode": "deliberation", "who": "member", "question": "Pass the bill?",
           "when": "$len($decisions('committee')) > 0"})


def test_a_committee_and_a_floor_deliberate_in_one_contract():
    assert errors(BICAMERAL) == []
    env = fg_env.load(BICAMERAL, seed=1)
    names = [s.name for s in env.contract.stages]
    assert names.index("committee_vote") < names.index("floor")
    assert do(env, "c1", [{"decision": "committee", "action": "propose", "text": "Report it"}])
    assert ev(env, "$pending_motion('committee').text") == "Report it"
    assert ev(env, "$pending_motion('floor')") is None
    assert "Report it" in ev(env, "$house($actor, 'committee')", actor=env.world.entities["c1"])
    result = env.run("random")
    assert result.status == "completed", result.error


def test_an_unnamed_read_of_several_mechanisms_names_them():
    ambiguous = {**BICAMERAL, "outputs": {"decided": {"expr": "$len($decisions())", "type": "int"}}}
    assert any("$decisions: there are several decision (deliberation) mechanisms (committee, floor): name one as the "
               "last argument" in e for e in issues(ambiguous))
    wrong = {**BICAMERAL, "outputs": {"decided": {"expr": "$len($decisions('senate'))", "type": "int"}}}
    assert any("'senate' is not a declared decision (deliberation) mechanism (decision (deliberation) mechanisms: "
               "committee, floor)" in e for e in issues(wrong))


def test_two_channel_networks_keep_their_messages_apart():
    contract = _with(town={"kind": "social", "mode": "channels", "who": "member", "rooms": ["plaza"]},
                     backroom={"kind": "social", "mode": "channels", "who": "committee_member", "rooms": ["lobby"]})
    assert errors(contract) == []
    env = fg_env.load(contract, seed=1)
    c2 = env.world.entities["c2"]
    assert ev(env, "$channels($actor, 'town')", actor=c2) == ["plaza"]
    assert ev(env, "$channels($actor, 'backroom')", actor=c2) == ["lobby"]
    assert do(env, "c1", [{"social": "backroom", "action": "say", "channel": "lobby", "text": "a quiet word"}])
    assert ev(env, "$unread('c2', null, 'backroom')") == 1 and ev(env, "$unread('c2', null, 'town')") == 0
    assert fg_env.load(contract, seed=2).run("random").status == "completed"


def test_two_mechanisms_generating_one_name_is_an_error_naming_both():
    memory = {"kind": "mind", "mode": "memory", "who": "member"}
    assert any("'m1' and 'm2' both generate actions 'note'" in e for e in errors(_with(m1=memory, m2=memory)))
    assert errors(_with(m1=memory, m2={**memory, "note": "jot", "recall": "search"})) == []


def test_an_authors_own_entry_still_overrides_a_generated_one():
    contract = {**_with(poll={"kind": "decision", "mode": "ballot", "who": "member", "options": ["yes", "no"]}),
                "views": {"poll_result": {"for": "member", "show": "Mine"}}}
    assert errors(contract) == []


def test_two_decks_get_their_own_card_ids_and_must_not_share_a_card_type():
    chance = {"kind": "game", "mode": "cards", "who": "member", "type": "chance",
              "deck": [{"name": "Advance to Go"}, {"name": "Pay tax"}]}
    chest = {**chance, "type": "chest"}
    env = fg_env.load(_with(chance=chance, chest=chest), seed=1)
    assert {"chance_advance_to_go", "chest_advance_to_go"} <= set(env.world.entities)
    assert any("decks 'chance' and 'chest' both hold cards of type 'card'" in e and '"type": "chest_card"' in e
               for e in errors(_with(chance={**chance, "type": "card"}, chest={**chest, "type": "card"})))


def test_a_deliberation_ends_the_run_only_when_asked():
    body = {"kind": "decision", "mode": "deliberation", "who": "member", "second": False, "min_debate": 0}
    motion = [{"decision": "hall", "action": "propose", "text": "Adjourn early"}]
    for config, finishes in (({}, False), ({"end": "decision"}, True)):
        env = fg_env.load(_with(hall={**body, **config}), seed=1)
        assert do(env, "m1", motion) and do(env, "m1", [{"decision": "hall", "action": "call_question"}])
        result = env.run(lambda wake: (wake.call("hall_vote", {"choice": "yes"}) if wake.stage == "hall_vote" else None,
                                       wake.end()))
        assert env.props["hall"]["decisions"][0]["passed"]
        assert (result.status == "ended" and result.ended_by == "hall") if finishes else result.status == "completed"


def test_check_lists_the_mechanisms_that_can_end_the_run():
    lines = generated_summary(_with(a={"kind": "decision", "mode": "deliberation", "who": "member"},
                                    b={"kind": "decision", "mode": "deliberation", "who": "member", "end": "adoption"}))
    assert not lines[0].endswith("can end the run") and lines[1].endswith(" · can end the run")
