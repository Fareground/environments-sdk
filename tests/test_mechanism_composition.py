"""Mechanisms compose: several of one kind share a contract, clashing names are refused, nothing ends the run unasked.
"""
import pytest

import fg_env
from fg_env.expr import compile_expr
from fg_env.mechanisms import generated_summary


def errors(contract):
    return [str(i) for i in fg_env.check(contract) if i.severity == "error"]


def issues(contract):
    return [str(i) for i in fg_env.check(contract)]


def ev(env, source, **vars):
    return compile_expr(source)(env.world.evaluation.scope(**vars))


def do(env, actor, effects):
    """Apply effects atomically (as `actor` when given); a refusal raises, as any refused world logic does."""
    env.rules.run_block(effects, {"actor": env.world.entities[actor]} if actor else {}, "test")
    return True


BASE = {"name": "Composed", "clock": {"rounds": 3},
        "types": {"member": {"agent": True}, "committee_member": {"extends": "member"}},
        "population": [{"type": "member", "count": 3, "id": "m{$i}"},
                       {"type": "committee_member", "count": 2, "id": "c{$i}"}]}


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


def test_two_mechanisms_generating_one_name_is_an_error_naming_both():
    memory = {"kind": "host", "mode": "memory", "who": "member"}
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


def test_a_mechanism_named_like_another_ones_records_is_an_error_naming_both():
    contract = {"name": "Clash", "clock": {"rounds": 2}, "types": {"trader": {"agent": True, "props": {"cash": 100}}},
                "entities": {"t1": {"type": "trader"}},
                "mechanisms": {"a": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 5},
                               "a_tape": {"kind": "decision", "mode": "deliberation", "who": "trader"}}}
    assert any("'a' and 'a_tape' both generate records 'a_tape'" in e for e in errors(contract))


def test_a_ballot_hooked_into_a_repeating_stage_is_counted_only_while_its_when_holds():
    """The tally obeys the ballot's `when`: counting again in later rounds would overwrite the result with an empty
    count."""
    contract = {"name": "Once", "clock": {"rounds": 3},
                "types": {"v": {"agent": True, "props": {"x": 0}}},
                "entities": {"a": {"type": "v"}, "b": {"type": "v"}, "c": {"type": "v"}},
                "actions": {"work": {"by": "v", "do": "$actor.x += 1"}},
                "stages": [{"name": "day", "actions": ["work", "e_vote", "e_abstain"]}],
                "mechanisms": {"e": {"kind": "decision", "mode": "ballot", "who": "v", "options": ["yes", "no"],
                                     "when": "$round == 1", "stage": "day"}},
                "outputs": {"winner": {"expr": "$world.e_result.winner if $world.e_result else 'none'",
                                       "series": True}}}

    def voter(wake):
        wake.call("e_vote", {"choice": "yes"}) if wake.round == 1 else wake.call("work", {})
        wake.end()

    result = fg_env.run(contract, {"v": voter}, seed=1)
    assert result.series["winner"] == ["yes", "yes", "yes"]


def test_voting_in_a_shared_stage_leaves_the_rest_of_the_turn_to_the_other_mechanisms():
    """The stage gives each mechanism hooked into it its share of the turn: voting first does not end it."""
    contract = {"name": "Floor", "clock": {"rounds": 1},
                "types": {"citizen": {"agent": True, "props": {"cash": 100}}},
                "entities": {"a": {"type": "citizen"}, "b": {"type": "citizen"}},
                "stages": [{"name": "floor", "turns": "sequential", "actions": []}],
                "mechanisms": {"vote": {"kind": "decision", "mode": "ballot", "who": "citizen",
                                        "options": ["yes", "no"], "stage": "floor"},
                               "pm": {"kind": "market", "mode": "prediction", "who": "citizen",
                                      "outcomes": ["yes", "no"], "stage": "floor"}}}
    said = {}

    def citizen(wake):
        said[wake.entity_id] = [wake.call("vote_vote", {"choice": "yes"}).ok,
                                wake.call("pm_buy", {"outcome": "yes", "spend": 5}).ok]
        wake.end()

    result = fg_env.run(contract, {"citizen": citizen}, seed=1)
    assert result.status == "completed", result.error
    assert said == {"a": [True, True], "b": [True, True]}
    assert result.stats["actions"] == 4


def test_a_declared_stage_gives_each_attached_mechanism_its_actions_and_the_author_one_more():
    """One rule for a stage's budget when it sets no `max_actions` (audit 11 mechanisms M3): the sum of what its
    attached mechanisms allow per turn, plus one for the author's own actions when it offers any — so trading cannot
    use up the turn the ballot needs. A stage with `max_actions` keeps it."""
    contract = {"name": "One stage", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"cash": 100}}},
                "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"talk": {"by": "p", "do": []}}, "stages": [{"name": "day"}],
                "mechanisms": {"v": {"kind": "decision", "mode": "ballot", "who": "p", "options": ["x", "y"],
                                     "stage": "day"},
                               "x": {"kind": "market", "mode": "order_book", "who": "p", "start_price": 50,
                                     "stage": "day"}}}
    alone = {name: spec.max_actions for name, spec in
             ((s.name, s) for s in fg_env.load({**contract, "stages": [], "mechanisms": {
                 key: {k: v for k, v in use.items() if k != "stage"} for key, use in contract["mechanisms"].items()}}
                                                ).contract.stage_list())}
    day = fg_env.load(contract).contract.stage_list()[0]
    assert day.max_actions == alone["v"] + alone["x"] + 1
    contract["stages"][0]["max_actions"] = 2
    assert fg_env.load(contract).contract.stage_list()[0].max_actions == 2


def _ledger_market(market, house):
    """Three traders and a ledger's cash, with one money-moving market (and a house holding units, when it has one)."""
    return {"name": "Money", "clock": {"rounds": 4},
            "types": {"trader": {"agent": True}, "house": {}},
            "entities": {"a": {"type": "trader"}, "b": {"type": "trader"}, "c": {"type": "trader"},
                         **({"hq": {"type": "house", "props": {"m_units": 3}}} if house else {})},
            "mechanisms": {"money": {"kind": "economy", "mode": "ledger", "who": ["trader", "house"] if house
                                     else ["trader"], "currencies": {"cash": {"start": 1000}}},
                           "m": {"kind": "market", "who": "trader", **market, **({"house": "hq"} if house else {})}},
            "outputs": {"conserved": "$conserved('money')"}}


_AUCTIONS = [{"mode": "auction", "format": fmt} for fmt in ("first_price", "second_price", "english", "uniform")] + [
    {"mode": "auction", "format": "dutch", "start_price": 400}]


@pytest.mark.parametrize("market, house", [
    *((auction, house) for auction in _AUCTIONS for house in (False, True)),
    ({"mode": "auction", "format": "double"}, False),
    ({"mode": "auction", "format": "first_price", "reverse": True, "reserve": 200}, True),
    ({"mode": "order_book", "start_price": 50}, False),
    ({"mode": "prediction", "outcomes": ["yes", "no"]}, False),
    ({"mode": "posted"}, False),
], ids=lambda value: value.get("format") or value.get("mode") if isinstance(value, dict) else f"house={value}")
def test_every_money_moving_market_conserves_a_ledgers_money_with_and_without_a_house(market, house):
    """A market's proceeds, escrows and vaults are counted by the ledger once each: a house paid by an auction used
    to be counted with the auction's running revenue too, so every run failed its conservation invariant (audit 12
    mechanisms H1)."""
    contract = _ledger_market(market, house)
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    for seed in range(3):
        result = fg_env.run(contract, "random", seed=seed)
        assert result.status == "completed" and result.outputs["conserved"] is True, (seed, result.error)


def test_a_sealed_bid_on_a_shared_stage_leaves_the_rest_of_the_turn_to_the_other_mechanisms():
    """A stage several mechanisms share gives the sum of their actions; a sealed bid ends the turn only on the
    auction's own stage (audit 12 mech M3)."""
    contract = {"name": "Floor", "clock": {"rounds": 1}, "types": {"trader": {"agent": True, "props": {"cash": 500}}},
                "entities": {"a": {"type": "trader"}, "b": {"type": "trader"}},
                "stages": [{"name": "floor", "turns": "simultaneous"}],
                "mechanisms": {"auc": {"kind": "market", "mode": "auction", "format": "second_price", "who": "trader",
                                       "stock": 1, "stage": "floor"},
                               "pm": {"kind": "market", "mode": "prediction", "who": "trader",
                                      "outcomes": ["up", "down"], "stage": "floor"}}}
    replies = []

    def play(wake):
        replies.append((wake.call("auc_bid", {"price": 20}).ended, wake.call("pm_buy", {"outcome": "up",
                                                                                         "spend": 5}).ok))
        wake.end()

    fg_env.run(contract, play, seed=1)
    assert replies == [(False, True), (False, True)]
