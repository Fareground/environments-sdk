"""Economy mechanisms: inventory, ledger, production, subscriptions, bookings, negotiation, labor
and supply chains — value is conserved, tools list only valid choices, runs are deterministic."""
import copy
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.sdk.expr import compile_expr

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"
#: Economy examples and the input that sets their length.
LONG_RUNS = {"corner_shop_town": {"days": 200}, "trade_negotiation": {"years": 200}, "crafting_village": {"days": 200},
             "beer_game_native": {"weeks": 200}}


def scripted(plan):
    """A participant playing ``plan``: {(entity_id, round): [(tool, args), ...]}; results are kept."""
    results, reasons = [], {}

    def participant(wake):
        reasons[(wake.entity_id, wake.round)] = wake.reason
        for tool, args in plan.get((wake.entity_id, wake.round), []):
            results.append((wake.entity_id, tool, wake.call(tool, args)))
        if not wake.done:
            wake.end()

    participant.results = results
    participant.reasons = reasons
    return participant


def ok(result):
    return [r for _, _, r in result]


def tool(env, entity_id, name):
    return env.actions.tool(env.world.entities[entity_id], name).input_schema["properties"]


def blocked(env, entity_id, name):
    return env.actions.blocked(env.world.entities[entity_id], name, {}, {})


def errors(contract):
    return [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

GOODS = {
    "name": "Goods",
    "clock": {"rounds": 3},
    "space": {"graph": {"nodes": ["square", "farm"], "edges": [["square", "farm"]]}},
    "types": {"person": {"agent": True, "props": {"hunger": 0}}},
    "entities": {"ana": {"type": "person", "name": "Ana", "at": "square", "props": {"goods": {"bread": 3, "apple": 2}}},
                 "ben": {"type": "person", "name": "Ben", "at": "square", "props": {"goods": {"apple": 3}}}},
    "mechanisms": {"goods": {"kind": "economy", "mode": "inventory", "who": "person", "capacity": 4,
                             "items": {"bread": {"value": 2, "on_consume": ["$actor.hunger -= $qty"]},
                                       "apple": {"value": 1},
                                       "sword": {"unique": True, "value": 30, "props": {"sharp": 1}}},
                             "actions": ["give", "consume", "drop", "pickup"]}},
    "stages": [{"name": "act", "turns": "sequential", "max_actions": 4, "max_calls": 10}],
}


def test_inventory_tools_offer_only_goods_you_hold_and_moves_conserve_them():
    env = fg_env.load(GOODS, seed=1)
    give = tool(env, "ana", "goods_give")
    assert give["item"]["enum"] == ["bread", "apple"]
    assert give["to"]["enum"] == ["ben"]
    assert "enum" not in tool(env, "ben", "goods_consume")["item"]  # Ben holds nothing consumable
    assert blocked(env, "ben", "goods_consume") == "You hold nothing you can consume"
    play = scripted({("ana", 1): [("goods_give", {"to": "ben", "item": "bread", "qty": 1}),
                                  ("goods_give", {"to": "ben", "item": "bread", "qty": 5}),
                                  ("goods_give", {"to": "ben", "item": "apple", "qty": 1}),
                                  ("goods_consume", {"item": "bread", "qty": 1})]})
    result = env.run(play, rounds=1)
    assert result.status != "failed", result.error
    moved, too_many, full, eaten = ok(play.results)
    assert moved.ok and "You gave 1 × bread to Ben" in moved.text
    assert not too_many.ok and "at most 2" in too_many.text
    assert not full.ok and "room for only 0 more" in full.text
    assert eaten.ok and env.entity("ana")["props"]["hunger"] == -1
    assert env.entity("ana")["props"]["goods"] == {"bread": 1, "apple": 2}
    assert env.entity("ben")["props"]["goods"] == {"apple": 3, "bread": 1}
    assert env.props["goods_supply"] == {"bread": 2, "apple": 5, "sword": 0}
    assert env.props["goods_flows"] == {"consumed": {"bread": -1}}


def test_unique_items_are_entities_that_change_owner():
    contract = copy.deepcopy(GOODS)
    contract["events"] = [{"at": 1, "do": [{"economy": "goods", "action": "make", "item": "sword", "to": "$entity(ben)",
                                            "source": "forge", "props": {"sharp": 3}}]}]
    env = fg_env.load(contract, seed=1)
    play = scripted({("ben", 1): [("goods_give", {"to": "ana", "item": "sword_1", "qty": 1}),
                                  ("goods_give", {"to": "ana", "item": "sword_9", "qty": 1})],
                     ("ana", 2): [("goods_consume", {"item": "bread", "qty": 2})],
                     ("ben", 2): [("goods_give", {"to": "ana", "item": "sword_1", "qty": 1})]})
    result = env.run(play, rounds=2)
    assert result.status != "failed", result.error
    full, unknown, eaten, given = ok(play.results)
    assert not full.ok and "Ana has room for only 0 more" in full.text
    assert not unknown.ok and "item must be one of" in unknown.text
    assert eaten.ok and given.ok, given.text
    sword = env.entity("sword_1")
    assert sword["type"] == "sword" and sword["props"]["owner"] == "ana" and sword["props"]["sharp"] == 3
    assert env.props["goods_supply"]["sword"] == 1 and env.props["goods_flows"]["forge"] == {"sword": 1}


def test_goods_dropped_on_the_ground_can_be_picked_up_where_they_lie():
    env = fg_env.load(GOODS, seed=1)
    play = scripted({("ana", 1): [("goods_drop", {"item": "bread", "qty": 2})],
                     ("ben", 1): [("goods_pickup", {"item": "bread", "qty": 1})]})
    env.run(play, rounds=1)
    assert all(r.ok for r in ok(play.results)), [r.text for r in ok(play.results)]
    assert env.props["goods_ground"] == {"square": {"bread": 1}}
    assert tool(env, "ben", "goods_pickup")["item"]["enum"] == ["bread"]


PANTRY = {
    "name": "Pantry",
    "clock": {"rounds": 4},
    "types": {"person": {"agent": True, "props": {"hunger": 0}}},
    "entities": {"cy": {"type": "person", "props": {"food": {"bread": 4}}}},
    "mechanisms": {"food": {"kind": "economy", "mode": "inventory", "who": "person", "items": {"bread": {"shelf_life": 2}},
                            "needs": {"person": {"bread": 1}}, "on_short": ["$it.hunger += $short"]}},
}


def test_needs_use_up_goods_every_round_and_perishables_spoil_oldest_first():
    env = fg_env.load(PANTRY, seed=1)
    result = env.run("idle")
    assert result.status == "completed", result.error
    assert env.entity("cy")["props"]["food"] == {}
    assert env.entity("cy")["props"]["hunger"] == 2
    assert env.props["food_flows"] == {"needs": {"bread": -2}, "spoiled": {"bread": -2}}
    assert any(e["kind"] == "food_spoiled" and "2 bread" in e["text"] for e in result.events)


CHEAT = {
    "name": "Cheat",
    "clock": {"rounds": 1},
    "types": {"person": {"agent": True}},
    "entities": {"ana": {"type": "person"}},
    "mechanisms": {"money": {"kind": "economy", "mode": "ledger", "who": "person", "currencies": {"cash": {"start": 10}}},
                   "goods": {"kind": "economy", "mode": "inventory", "who": "person", "items": {"bread": {}}}},
    "actions": {"print_money": {"by": "person", "do": ["$actor.cash += 5"], "terminal": True},
                "conjure": {"by": "person", "do": ["$actor.goods = {bread: 9}"], "terminal": True}},
}


@pytest.mark.parametrize("action", ["print_money", "conjure"])
def test_value_created_outside_a_named_source_fails_the_run(action):
    def cheat(wake):
        wake.call(action, {})

    result = fg_env.load(CHEAT, seed=1).run(cheat)
    assert result.status == "failed" and "$conserved" in result.error


def test_inventory_config_errors_say_what_to_fix():
    both = copy.deepcopy(CHEAT)
    both["mechanisms"]["goods"]["items"] = {"cash": {}}
    assert any("'cash' is already declared by 'money'" in i.message for i in errors(both))
    ground = copy.deepcopy(CHEAT)
    ground["mechanisms"]["goods"]["actions"] = ["drop"]
    assert any("drop and pickup need a declared space" in i.message for i in errors(ground))
    unique = copy.deepcopy(CHEAT)
    unique["mechanisms"]["goods"]["items"] = {"ring": {"unique": True, "consumable": True}}
    assert any("cannot be consumable" in i.message for i in errors(unique))
    holders = copy.deepcopy(CHEAT)
    holders["mechanisms"]["goods"]["who"] = "robot"
    assert any(i.path == "mechanisms.goods.who" for i in errors(holders))


def test_an_old_economy_kind_says_its_family_and_mode():
    old = copy.deepcopy(CHEAT)
    old["mechanisms"]["money"] = {"kind": "ledger", "holders": "person", "currencies": {"cash": {}}}
    issue = next(i for i in errors(old) if i.path == "mechanisms.money.kind")
    assert issue.message == "'ledger' is now kind 'economy' with mode 'ledger'"
    assert '"kind": "economy", "mode": "ledger"' in issue.fix


def test_a_typo_or_an_old_field_name_names_the_economy_mode_and_its_fields():
    typo = copy.deepcopy(CHEAT)
    typo["mechanisms"]["goods"]["capacty"] = 3
    issue = next(i for i in errors(typo) if i.path == "mechanisms.goods.capacty")
    assert issue.message == "`capacty` is not a field of `economy` mode `inventory`"
    assert issue.fix.startswith("did you mean 'capacity'?")
    renamed = copy.deepcopy(CHEAT)
    renamed["mechanisms"]["money"]["tools"] = ["pay"]
    assert any(i.path.startswith("mechanisms.money.tools") for i in errors(renamed))  # the tool list is `actions` now


def test_economy_actions_check_their_own_keys():
    def op(effect):
        return [(i.path, i.message, i.fix) for i in errors({**CHEAT, "events": [{"do": [effect]}]})]

    pay = {"economy": "money", "action": "pay", "from": "$entity(ana)", "to": "$entity(ana)", "amount": 1}
    assert op(pay) == []
    assert any(m == "'qty' is not part of `economy.pay`" for _, m, _ in op({**pay, "qty": 2}))
    assert any(m == "`economy.give` needs `item`" for _, m, _ in op({"economy": "goods", "action": "give", "from": "$entity(ana)",
                                                                     "to": "$entity(ana)"}))
    path, message, fix = op({"economy": "money", "action": "count"})[0]
    assert path.endswith(".action") and message == "'count' is not an action of money (economy ledger)"
    assert fix == "actions: pay, mint, burn, lend, repay"
    path, message, fix = op({"economy": "money", "action": "mint", "currency": "csh", "to": "$entity(ana)", "amount": 1,
                             "source": "gift"})[0]
    assert (path.endswith(".currency"), message, fix) == (True, "'csh' is not a currency of money", "did you mean 'cash'?")
    path, message, fix = op({"economy": "goods", "action": "make", "item": "bred", "to": "$entity(ana)", "source": "oven"})[0]
    assert (path.endswith(".item"), message, fix) == (True, "'bred' is not an item of goods", "did you mean 'bread'?")
    assert any("declares no loans" in m for _, m, _ in op({"economy": "money", "action": "repay", "loan": "x", "amount": 1}))
    assert any("has no ground" in m for _, m, _ in op({"economy": "goods", "action": "drop", "item": "bread", "from": "$entity(ana)"}))
    _, _, fix = op({"pay": "cash", "from": "$entity(ana)", "to": "$entity(ana)", "amount": 1})[0]
    assert fix.startswith('`pay` is now the `economy` op: {"economy": "<mechanism>", "action": "pay"')


def test_tools_one_offers_an_inventory_as_a_single_tool():
    contract = copy.deepcopy(GOODS)
    contract["mechanisms"]["goods"]["tools"] = "one"
    env = fg_env.load(contract, seed=1)
    offered = []

    def play(wake):
        tools = {t.name: t for t in wake.tools}
        offered.append((wake.entity_id, tools))
        if wake.entity_id == "ana":
            assert wake.call("goods", {"action": "give", "to": "ben", "item": "bread", "qty": 1}).ok
        wake.end()

    env.run(play, rounds=1)
    entity_id, tools = offered[0]
    assert entity_id == "ana" and "goods_give" not in tools
    assert {"give", "consume", "drop"} <= set(tools["goods"].input_schema["properties"]["action"]["enum"])
    assert env.entity("ben")["props"]["goods"] == {"apple": 3, "bread": 1}


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------

BANKING = {
    "name": "Banking",
    "clock": {"rounds": 4},
    "types": {"person": {"agent": True, "props": {"defaults": 0}}, "bank": {}},
    "entities": {"ana": {"type": "person", "name": "Ana"}, "ben": {"type": "person", "name": "Ben", "props": {"cash": 0}},
                 "vault": {"type": "bank", "name": "Vault", "props": {"cash": 500}}},
    "mechanisms": {"money": {
        "kind": "economy", "mode": "ledger", "who": ["person", "bank"], "currencies": {"cash": {"start": 50, "credit": 10}},
        "sources": {"ubi": {"to": "person", "amount": 5, "every": 2, "start": 2}},
        "taxes": {"vat": {"rate": 0.1, "on": "payer"}, "levy": {"rate": 0.2}},
        "loans": {"lenders": "bank", "borrowers": "person", "rate_min": 0.1, "rate_max": 0.1, "max_term": 3,
                  "on_default": ["$borrower.defaults += 1"]},
        "actions": ["pay"]}},
    "actions": {"shop": {"by": "person", "params": {"amount": "number"},
                         "do": [{"economy": "money", "action": "pay", "from": "$actor", "to": "$entity(vault)", "amount": "$params.amount",
                                  "tax": "vat"}]},
                "earn": {"by": "person", "do": [{"economy": "money", "action": "pay", "currency": "cash", "from": "$entity(vault)",
                                                 "to": "$actor", "amount": 10, "tax": "levy"}]}},
    "stages": [{"name": "act", "turns": "sequential", "max_actions": 4, "max_calls": 10}],
}


def test_payments_use_credit_but_never_pass_it_and_taxes_leave_through_their_sink():
    env = fg_env.load(BANKING, seed=1)
    assert tool(env, "ana", "money_pay")["amount"]["maximum"] == 60
    play = scripted({("ana", 1): [("money_pay", {"to": "ben", "amount": 55}), ("money_pay", {"to": "ben", "amount": 6}),
                                  ("shop", {"amount": 20}), ("shop", {"amount": 4}), ("earn", {})]})
    env.run(play, rounds=1)
    paid, over_bound, over_credit, shopped, earned = ok(play.results)
    assert paid.ok and not over_bound.ok and "amount must be at most 5" in over_bound.text
    assert not over_credit.ok and "Ana has only -5 cash (credit 10); 22 is needed" in over_credit.text
    assert shopped.ok and earned.ok
    ana, vault = env.entity("ana")["props"], env.entity("vault")["props"]
    assert ana["cash"] == pytest.approx(-5 - 4.4 + 8)
    assert vault["cash"] == pytest.approx(500 + 4 - 10)
    assert env.props["money_flows"] == {"vat": {"cash": -0.4}, "levy": {"cash": -2}}
    assert env.props["money_supply"]["cash"] == pytest.approx(50 + 0 + 500 - 2.4)


def test_sources_pay_on_schedule_with_add_top_up_and_reset():
    env = fg_env.load(BANKING, seed=1)
    env.run("idle", rounds=3)
    assert env.entity("ben")["props"]["cash"] == 5  # paid in round 2 only (every 2 rounds from round 2)
    env.run("idle", rounds=1)
    assert env.entity("ben")["props"]["cash"] == 10
    # Sources pay at the start of a round, before anyone acts: round 1 and round 4 here.
    for mode, amount, after_one, after_four in (("top_up", 50, (30, 70), (50, 70)), ("reset", 30, (10, 50), (30, 30))):
        contract = copy.deepcopy(BANKING)
        contract["mechanisms"]["money"]["sources"] = {"allowance": {"to": "person", "amount": amount, "mode": mode, "every": 3}}
        env = fg_env.load(contract, seed=1)
        env.run(scripted({("ana", 1): [("money_pay", {"to": "ben", "amount": 20})]}), rounds=1)
        assert (env.entity("ana")["props"]["cash"], env.entity("ben")["props"]["cash"]) == after_one
        env.run("idle", rounds=3)
        assert (env.entity("ana")["props"]["cash"], env.entity("ben")["props"]["cash"]) == after_four
        if mode == "reset":
            assert env.props["money_flows"]["allowance expired"] == {"cash": -110}


def test_loans_accrue_interest_collect_when_due_and_default_when_unpaid():
    env = fg_env.load(BANKING, seed=1)
    lender = tool(env, "ana", "money_borrow")["lender"]
    assert lender["enum"] == ["vault"]
    play = scripted({("ana", 1): [("money_borrow", {"lender": "vault", "amount": 20, "term": 2})],
                     ("ben", 1): [("money_borrow", {"lender": "vault", "amount": 10, "term": 2}),
                                  ("money_pay", {"to": "ana", "amount": 20})]})
    env.run(play, rounds=3)
    assert all(r.ok for r in ok(play.results)), [r.text for r in ok(play.results)]
    loans = {e["props"]["borrower"]: e["props"] for e in env.entities("money_loan")}
    assert loans["ana"]["status"] == "repaid" and loans["ana"]["paid"] == pytest.approx(24.2)
    assert loans["ben"]["status"] == "defaulted" and loans["ben"]["written_off"] > 0
    assert env.entity("ben")["props"]["defaults"] == 1
    assert env.props["money_loans"]["repaid_count"] == 1 and env.props["money_loans"]["defaulted_count"] == 1


def test_a_refused_on_default_hook_never_undoes_the_rest_of_the_loans_tick():
    contract = copy.deepcopy(BANKING)
    contract["mechanisms"]["money"]["loans"]["on_default"] = [{"fail": "The bailiffs found nothing."}]
    contract["entities"]["ana"]["props"] = {"cash": 0}
    env = fg_env.load(contract, seed=1)
    borrow = [("money_borrow", {"lender": "vault", "amount": 10, "term": 1}), ("money_pay", {"to": "vault", "amount": 10})]
    result = env.run(scripted({("ana", 1): borrow, ("ben", 1): borrow}), rounds=2)
    assert result.status != "failed", result.error
    assert [loan["props"]["status"] for loan in env.entities("money_loan")] == ["defaulted", "defaulted"]
    assert env.props["money_loans"]["defaulted_count"] == 2
    refused = [e for e in result.events if e["kind"] == "mechanism_refused"]
    assert len(refused) == 2 and "The bailiffs found nothing." in refused[0]["text"]


def test_net_worth_counts_money_goods_and_loans():
    contract = copy.deepcopy(BANKING)
    contract["mechanisms"]["goods"] = {"kind": "economy", "mode": "inventory", "who": "person", "items": {"bread": {"value": 2}},
                                       "start": {"bread": 3}}
    contract["world"] = {"worth": 0, "priced": 0}
    contract["events"] = [{"phase": "end", "do": ["$world.worth = $net_worth($entity(ana))",
                                                  "$world.priced = $net_worth($entity(ana), {bread: 5})"]}]
    env = fg_env.load(contract, seed=1)
    env.run(scripted({("ana", 1): [("money_borrow", {"lender": "vault", "amount": 20, "term": 3})]}), rounds=1)
    assert env.props["worth"] == pytest.approx(70 + 6 - 20)
    assert env.props["priced"] == pytest.approx(70 + 15 - 20)


# ---------------------------------------------------------------------------
# production
# ---------------------------------------------------------------------------

CRAFT = {
    "name": "Craft",
    "clock": {"rounds": 4},
    "space": {"graph": {"nodes": ["forest", "mill"], "edges": [["forest", "mill"]]}},
    "types": {"villager": {"agent": True}},
    "entities": {"ivy": {"type": "villager", "name": "Ivy", "at": "forest", "props": {"goods": {"grain": 5}}},
                 "joe": {"type": "villager", "name": "Joe", "at": "mill"},
                 "kim": {"type": "villager", "name": "Kim", "at": "forest", "props": {"goods": {"wood": 3}}}},
    "mechanisms": {
        "goods": {"kind": "economy", "mode": "inventory", "who": "villager", "capacity": 12,
                  "items": {"grain": {}, "flour": {}, "wood": {}, "axe": {"unique": True}}},
        "money": {"kind": "economy", "mode": "ledger", "who": "villager", "currencies": {"coin": {"start": 3}}},
        "craft": {"kind": "economy", "mode": "production", "who": "villager", "inventory": "goods",
                  "skills": {"milling": {"xp_per_level": 2, "max_level": 3}},
                  "recipes": {
                      "chop": {"outputs": {"wood": "1 + $skill($actor, milling)"}, "at": "forest"},
                      "mill": {"inputs": {"grain": 2}, "outputs": {"flour": 1}, "rounds": 1, "skill": "milling", "xp": 2,
                               "cost": {"coin": 1}},
                      "fine_mill": {"inputs": {"grain": 1}, "outputs": {"flour": 1}, "skill": "milling", "level": 1},
                      "carve": {"inputs": {"wood": 3}, "outputs": {"axe": 1}},
                      "fell": {"outputs": {"wood": 2}, "tools": {"axe": 1}, "at": "forest"}}}},
    "stages": [{"name": "work", "turns": "sequential", "max_actions": 4, "max_calls": 10}],
}


def test_production_lists_makeable_recipes_runs_jobs_in_slots_and_levels_skills():
    env = fg_env.load(CRAFT, seed=1)
    assert tool(env, "ivy", "craft_start")["recipe"]["enum"] == ["chop", "mill"]
    assert blocked(env, "joe", "craft_start").startswith("You cannot make anything now")
    play = scripted({("ivy", 1): [("craft_start", {"recipe": "chop"}), ("craft_start", {"recipe": "mill", "qty": 3}),
                                  ("craft_start", {"recipe": "mill", "qty": 2}), ("craft_start", {"recipe": "mill"})],
                     ("ivy", 2): [("craft_start", {"recipe": "fine_mill"})]})
    result = env.run(play, rounds=2)
    assert result.status != "failed", result.error
    chop, too_many, milling, busy, fine = ok(play.results)
    assert chop.ok and "done at once" in chop.text
    assert not too_many.ok and "qty must be at most 2" in too_many.text
    assert milling.ok and "2 × mill: ready in 1 round" in milling.text
    assert not busy.ok and "must be one of chop" in busy.text  # the only slot is taken
    assert fine.ok, fine.text
    ivy = env.entity("ivy")["props"]
    assert ivy["goods"] == {"flour": 3, "wood": 1}
    assert ivy["skills"] == {"milling": 1} and ivy["skill_xp"] == {"milling": 2}
    assert env.props["goods_flows"] == {"chop": {"wood": 1}, "mill": {"grain": -4, "flour": 2},
                                        "fine_mill": {"grain": -1, "flour": 1}}
    assert env.props["money_flows"] == {"mill": {"coin": -2}}
    assert env.props["craft_made"] == {"chop": 1, "mill": 2, "fine_mill": 1}
    assert any(e["kind"] == "craft_done" and "2 flour" in e["text"] and "milling is now level 1" in e["text"]
               for e in result.events)


def test_unique_outputs_and_tools_in_hand():
    env = fg_env.load(CRAFT, seed=1)
    play = scripted({("kim", 1): [("craft_start", {"recipe": "carve"}), ("craft_start", {"recipe": "fell"})]})
    env.run(play, rounds=1)
    assert all(r.ok for r in ok(play.results)), [r.text for r in ok(play.results)]
    assert [e["props"]["owner"] for e in env.entities("axe")] == ["kim"]
    assert env.entity("kim")["props"]["goods"] == {"wood": 2}
    assert env.props["goods_flows"]["carve"] == {"wood": -3, "axe": 1}


def test_a_finished_job_waits_while_its_output_does_not_fit():
    contract = copy.deepcopy(CRAFT)
    contract["entities"]["ivy"]["props"]["goods_capacity"] = 5
    contract["events"] = [{"at": 2, "do": [{"economy": "goods", "action": "make", "item": "wood", "to": "$entity(ivy)", "qty": 4,
                                         "source": "gift"}]},
                          {"at": 3, "do": [{"economy": "goods", "action": "use", "item": "wood", "from": "$entity(ivy)", "qty": 4,
                                         "sink": "fire"}]}]
    env = fg_env.load(contract, seed=1)
    result = env.run(scripted({("ivy", 1): [("craft_start", {"recipe": "mill", "qty": 2})]}), rounds=2)
    assert [j["props"]["status"] for j in env.entities("craft_job")] == ["waiting"]
    assert any(e["kind"] == "craft_waiting" and "has room for only" in e["text"] for e in result.events)
    env.run("idle", rounds=1)
    assert env.entities("craft_job") == [] and env.entity("ivy")["props"]["goods"] == {"grain": 1, "flour": 2}


def test_production_config_errors_say_what_to_fix():
    def broken(**recipe):
        contract = copy.deepcopy(CRAFT)
        contract["mechanisms"]["craft"]["recipes"] = {"x": {"outputs": {"flour": 1}, **recipe}}
        return [i.message for i in errors(contract)]

    assert any("'gold' is not an item of inventory 'goods'" in m for m in broken(inputs={"gold": 1}))
    assert any("skill 'magic' is not declared" in m for m in broken(skill="magic"))
    assert any("'gems' is not a declared currency" in m for m in broken(cost={"gems": 1}))
    no_space = copy.deepcopy(CRAFT)
    del no_space["space"]
    for entity in no_space["entities"].values():
        entity.pop("at")
    assert any("`at` needs a declared space" in i.message for i in errors(no_space))
    missing = copy.deepcopy(CRAFT)
    missing["mechanisms"]["craft"]["inventory"] = "stock"
    assert any("'stock' is not a declared economy (inventory) mechanism" in i.message for i in errors(missing))


# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------

CAFE = {
    "name": "Cafe",
    "clock": {"rounds": 8},
    "types": {"household": {"agent": True}, "cafe": {"agent": True}},
    "entities": {"hal": {"type": "household", "name": "Hal", "props": {"cash": 100}},
                 "ida": {"type": "household", "name": "Ida", "props": {"cash": 25}},
                 "bean": {"type": "cafe", "name": "Bean Bar"}},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": ["household", "cafe"], "currencies": {"cash": {}}},
        "coffee": {"kind": "agreements", "mode": "subscriptions", "who": "household", "currency": "cash", "providers": "cafe", "price_max": 100,
                   "plans": {"club": {"provider": "bean", "name": "Coffee Club", "price": 20, "period": 3, "trial": 2}}}},
    "outputs": {"hal_member": "$subscribed($entity(hal), bean)", "ida_member": "$subscribed($entity(ida), club)"},
    "stages": [{"name": "day", "turns": "sequential", "max_actions": 3, "max_calls": 8}],
}


def test_subscriptions_trial_convert_renew_reprice_cancel_and_lapse():
    env = fg_env.load(CAFE, seed=1)
    assert errors(CAFE) == []
    assert tool(env, "hal", "coffee_subscribe")["plan"]["enum"] == ["club"]
    play = scripted({("hal", 1): [("coffee_subscribe", {"plan": "club"})],
                     ("ida", 1): [("coffee_subscribe", {"plan": "club"})],
                     ("ida", 2): [("coffee_cancel", {"subscription": "coffee_sub_2"})],
                     ("bean", 2): [("coffee_set_price", {"plan": "club", "price": 25})],
                     ("ida", 4): [("coffee_subscribe", {"plan": "club"})]})
    result = env.run(play, rounds=7)
    assert result.status != "failed", result.error
    assert all(r.ok for r in ok(play.results)), [r.text for r in ok(play.results)]
    assert blocked(env, "hal", "coffee_subscribe") == "there is no coffee_plan you can choose for plan right now"
    assert env.props["coffee_stats"] == {"started": 3, "trials": 2, "converted": 1, "renewed": 1, "cancelled": 1,
                                         "lapsed": 1, "revenue": 75}
    assert env.entity("bean")["props"]["cash"] == 75 and env.entity("hal")["props"]["cash"] == 50
    assert env.entity("club")["props"]["subscribers"] == 1
    assert play.reasons[("hal", 3)] == "Your Coffee Club trial ended and you were charged."
    assert play.reasons[("ida", 3)] == "Coffee Club changed its price."
    assert play.reasons[("ida", 7)] == "Your Coffee Club subscription lapsed."
    assert result.outputs["hal_member"] is True and result.outputs["ida_member"] is False
    assert any(e["kind"] == "coffee_price" and "from 20 to 25" in e["text"] for e in result.events)


# ---------------------------------------------------------------------------
# bookings
# ---------------------------------------------------------------------------

DINING = {
    "name": "Dining",
    "clock": {"rounds": 5},
    "types": {"guest": {"agent": True, "props": {"vip": 0}}, "bistro": {}},
    "entities": {"g1": {"type": "guest", "name": "Gil"}, "g2": {"type": "guest", "name": "Gus", "props": {"vip": 5}},
                 "g3": {"type": "guest", "name": "Gia", "props": {"vip": 1}},
                 "bistro": {"type": "bistro", "name": "Bistro", "props": {"cash": 0}}},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": ["guest", "bistro"], "currencies": {"cash": {"start": 100}}},
        "dining": {"kind": "agreements", "mode": "bookings", "who": "guest", "currency": "cash", "refund": 0.5,
                   "resources": {"tables": {"provider": "bistro", "capacity": 4, "price": 10, "horizon": 2, "max_party": 4}}}},
    "stages": [{"name": "evening", "turns": "sequential", "max_actions": 3, "max_calls": 8}],
}


def test_bookings_fill_slots_waitlist_promote_on_cancel_and_serve():
    assert errors(DINING) == []
    env = fg_env.load(DINING, seed=1)
    play = scripted({("g1", 1): [("dining_book", {"resource": "tables", "ahead": 2, "party": 3}),
                                 ("dining_book", {"resource": "tables", "ahead": 2, "party": 1})],
                     ("g2", 1): [("dining_book", {"resource": "tables", "ahead": 2, "party": 2})],
                     ("g3", 1): [("dining_book", {"resource": "tables", "ahead": 2, "party": 1})],
                     ("g1", 2): [("dining_cancel", {"booking": "dining_booking_1"})]})
    result = env.run(play, rounds=3)
    assert result.status != "failed", result.error
    booked, twice, waiting, last, cancelled = ok(play.results)
    assert booked.ok and booked.text == "Booked 3 place(s) at Tables for round 3, paid 30 cash."
    assert not twice.ok and "already have a booking at Tables for round 3" in twice.text
    assert waiting.ok and waiting.text == "Tables for round 3 is full; you are number 1 in line."
    assert last.ok and cancelled.ok
    assert play.reasons[("g2", 2)] == "You got a place at Tables."
    assert env.props["dining_stats"] == {"booked": 2, "waitlisted": 1, "promoted": 1, "served": 3, "cancelled": 1,
                                         "abandoned": 0, "turned_away": 0, "offered": 12, "revenue": 45}
    assert env.entity("bistro")["props"]["cash"] == 45 and env.entity("g1")["props"]["cash"] == 85


def test_queues_serve_by_priority_and_impatient_guests_give_up():
    contract = copy.deepcopy(DINING)
    contract["mechanisms"]["dining"].update(format="queue", order="priority", priority="$it.vip", patience=1)
    contract["mechanisms"]["dining"]["resources"]["tables"].update(capacity=1, price=5)
    env = fg_env.load(contract, seed=1)
    assert "ahead" not in tool(env, "g1", "dining_book")
    join = [("dining_book", {"resource": "tables"})]
    env.run(scripted({("g1", 1): join, ("g2", 1): join, ("g3", 1): join}), rounds=3)
    statuses = {e["props"]["guest"]: e["props"]["status"] for e in env.entities("dining_booking")}
    assert statuses == {"g2": "served", "g1": "abandoned", "g3": "abandoned"}
    assert env.props["dining_stats"]["served"] == 1 and env.props["dining_stats"]["offered"] == 3
    assert env.entity("bistro")["props"]["cash"] == 5


def test_subscription_and_booking_config_errors():
    wrong = copy.deepcopy(CAFE)
    wrong["mechanisms"]["coffee"]["currency"] = "gold"
    assert any("'gold' is not a declared currency" in i.message for i in errors(wrong))
    unpaid = copy.deepcopy(DINING)
    unpaid["mechanisms"]["dining"].pop("currency")
    assert any("has a price but no currency or provider" in i.message for i in errors(unpaid))
    priority = copy.deepcopy(DINING)
    priority["mechanisms"]["dining"]["order"] = "priority"
    assert any("priority order needs a `priority` expression" in i.message for i in errors(priority))


def test_an_old_agreements_kind_or_a_mistyped_field_says_what_it_is_now():
    old = copy.deepcopy(DINING)
    old["mechanisms"]["dining"]["kind"] = "bookings"
    issue = next(i for i in errors(old) if i.path == "mechanisms.dining.kind")
    assert issue.message == "'bookings' is now kind 'agreements' with mode 'bookings'"
    typo = copy.deepcopy(DINING)
    typo["mechanisms"]["dining"]["formt"] = "queue"
    issue = next(i for i in errors(typo) if i.path == "mechanisms.dining.formt")
    assert issue.message == "`formt` is not a field of `agreements` mode `bookings`"
    assert issue.fix.startswith("did you mean 'format'?")


def test_tools_one_offers_bookings_as_a_single_tool():
    contract = copy.deepcopy(DINING)
    contract["mechanisms"]["dining"]["tools"] = "one"
    env = fg_env.load(contract, seed=1)
    offered = {}

    def play(wake):
        offered[wake.entity_id] = {t.name: t for t in wake.tools}
        if wake.entity_id == "g1":
            assert wake.call("dining", {"action": "book", "resource": "tables", "ahead": 1, "party": 2}).ok
        wake.end()

    env.run(play, rounds=1)
    assert "dining_book" not in offered["g1"] and "book" in offered["g1"]["dining"].input_schema["properties"]["action"]["enum"]
    assert [b["props"]["status"] for b in env.entities("dining_booking")] == ["booked"]


# ---------------------------------------------------------------------------
# negotiation
# ---------------------------------------------------------------------------

TRADE = {
    "name": "Trade talks",
    "clock": {"rounds": 8},
    "types": {"country": {"agent": True, "props": {"standing": 1}}},
    "entities": {"ar": {"type": "country", "name": "Arland", "props": {"credits": 500, "stock": {"steel": 40}}},
                 "bo": {"type": "country", "name": "Borin", "props": {"credits": 500}},
                 "cy": {"type": "country", "name": "Cyra", "props": {"credits": 500}}},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": "country", "currencies": {"credits": {}}},
        "stock": {"kind": "economy", "mode": "inventory", "who": "country", "items": {"steel": {"value": 5}}},
        "trade": {"kind": "agreements", "mode": "negotiation", "who": "country", "deadline": 3, "max_depth": 2, "reservation": 10,
                  "value": "$terms.price * $terms.quota * (1 if $party.id == 'ar' else -1)",
                  "issues": {"price": {"min": 1, "max": 20, "unit": "credits"}, "quota": {"type": "int", "min": 0, "max": 20},
                             "years": {"type": "int", "min": 1, "max": 3}},
                  "obligations": [
                      {"label": "delivery", "from": "$entity(ar)", "to": "$entity(bo)", "give": "steel", "amount": "$terms.quota",
                       "times": "$terms.years", "manual": True},
                      {"label": "payment", "from": "$entity(bo)", "to": "$entity(ar)", "pay": "credits",
                       "amount": "$terms.price * $terms.quota", "times": "$terms.years"}],
                  "breach": {"penalty": 50, "terminate": True, "on_breach": ["$breacher.standing -= 1"]}}},
    "stages": [{"name": "talks", "turns": "sequential", "max_actions": 3, "max_calls": 8}],
}


def test_negotiation_counter_offers_sign_a_deal_executed_over_rounds_and_punish_a_breach():
    assert errors(TRADE) == []
    env = fg_env.load(TRADE, seed=1)
    propose = tool(env, "ar", "trade_propose")
    assert (propose["price"]["minimum"], propose["price"]["maximum"], propose["quota"]["type"]) == (1, 20, "integer")
    play = scripted({("ar", 1): [("trade_propose", {"to": "bo", "price": 10, "quota": 5, "years": 3, "note": "fair"})],
                     ("bo", 1): [("trade_counter", {"offer": "trade_offer_1", "price": 8, "quota": 5, "years": 3})],
                     ("ar", 2): [("trade_accept", {"offer": "trade_offer_2"})],
                     ("ar", 3): [("trade_fulfill", {"duty": "trade_duty_1"})]})
    result = env.run(play, rounds=4)
    assert result.status != "failed", result.error
    assert all(r.ok for r in ok(play.results)), [r.text for r in ok(play.results)]
    assert play.reasons[("ar", 2)] == "Borin made you an offer."
    assert blocked(env, "ar", "trade_propose") == "A deal has been signed"
    assert env.props["trade_stats"] == {"offers": 2, "counters": 1, "rejected": 0, "withdrawn": 0, "expired": 0, "deals": 1,
                                        "duties_done": 2, "breaches": 1, "penalties": 50}
    ar, bo = env.entity("ar")["props"], env.entity("bo")["props"]
    assert (ar["credits"], bo["credits"]) == (500 + 40 - 50, 500 - 40 + 50)
    assert (ar["stock"], bo["stock"]) == ({"steel": 35}, {"steel": 5}) and ar["standing"] == 0
    assert [e["props"]["status"] for e in env.entities("trade_deal")] == ["terminated"]
    assert [d["props"]["status"] for d in env.entities("trade_duty")] == ["done", "breached", "cancelled", "done", "cancelled", "cancelled"]


def test_offers_show_private_worth_and_walk_away_only_to_their_owner():
    env = fg_env.load(TRADE, seed=1)
    env.run(scripted({("ar", 1): [("trade_propose", {"to": "bo", "price": 10, "quota": 5, "years": 1})]}), rounds=1)

    def seen(entity_id):
        actor = env.world.entities[entity_id]
        lines = (env.perception.render_view(name, view, actor) for name, view in env.contract.views.items())
        return "\n".join(line for line in lines if line)

    ar, bo = seen("ar"), seen("bo")
    assert "Your walk-away value: 10" in ar and "worth 50 to you" in ar
    assert "worth -50 to you" in bo and "price 10 credits, quota 5, years 1" in bo
    assert "worth 50" not in bo  # the other side's worth is never shown
    assert tool(env, "bo", "trade_accept")["offer"]["enum"] == ["trade_offer_1"]
    assert tool(env, "ar", "trade_accept")["offer"]["enum"] == []  # nothing is open to Arland


def test_a_refused_on_breach_hook_never_undoes_the_rest_of_the_negotiation_tick():
    contract = copy.deepcopy(TRADE)
    contract["mechanisms"]["trade"].update(expires=1, once=False)
    contract["mechanisms"]["trade"]["breach"]["on_breach"] = [{"fail": "No one to blame."}]
    env = fg_env.load(contract, seed=1)
    play = scripted({("ar", 1): [("trade_propose", {"to": "bo", "price": 10, "quota": 5, "years": 1})],
                     ("bo", 1): [("trade_accept", {"offer": "trade_offer_1"})],
                     ("cy", 1): [("trade_propose", {"to": "ar", "price": 3, "quota": 1, "years": 1})]})
    result = env.run(play, rounds=2)
    assert result.status != "failed", result.error
    assert env.entity("trade_offer_2")["props"]["status"] == "expired"  # unrelated work in the same tick stands
    assert [d["props"]["status"] for d in env.entities("trade_duty")] == ["breached", "cancelled"]
    assert env.props["trade_stats"]["breaches"] == 1 and env.entity("ar")["props"]["standing"] == 1
    assert any(e["kind"] == "mechanism_refused" and "No one to blame." in e["text"] for e in result.events)


def test_counter_depth_and_the_deadline_expire_talks_without_a_deal():
    env = fg_env.load(TRADE, seed=1)
    terms = {"price": 9, "quota": 4, "years": 1}
    play = scripted({("ar", 1): [("trade_propose", {"to": "bo", **terms})],
                     ("bo", 2): [("trade_counter", {"offer": "trade_offer_1", **terms})],
                     ("ar", 3): [("trade_counter", {"offer": "trade_offer_2", **terms})],
                     ("bo", 3): [("trade_counter", {"offer": "trade_offer_3", **terms})]})
    env.run(play, rounds=4)
    *answered, too_deep = ok(play.results)
    assert all(r.ok for r in answered) and not too_deep.ok
    assert "there is no trade_offer you can choose for offer" in too_deep.text  # offers at the depth limit are not listed
    assert [o["props"]["status"] for o in env.entities("trade_offer")] == ["countered", "countered", "expired"]
    assert env.props["trade_stats"]["expired"] == 1 and env.entities("trade_deal") == []


def test_coalition_offers_bind_only_when_every_recipient_accepts():
    contract = copy.deepcopy(TRADE)
    contract["mechanisms"]["trade"].update(coalition=True, obligations=[], deadline=None)
    env = fg_env.load(contract, seed=1)
    play = scripted({("ar", 1): [("trade_propose", {"recipients": ["bo", "cy"], "price": 5, "quota": 2, "years": 1})],
                     ("bo", 1): [("trade_accept", {"offer": "trade_offer_1"})],
                     ("cy", 2): [("trade_accept", {"offer": "trade_offer_1"})]})
    env.run(play, rounds=1)
    assert env.entities("trade_deal") == [] and env.entity("trade_offer_1")["props"]["accepted_by"] == ["bo"]
    env.run(play, rounds=1)
    deals = env.entities("trade_deal")
    assert [d["props"]["parties"] for d in deals] == [["ar", "bo", "cy"]] and deals[0]["props"]["status"] == "completed"


def test_negotiation_config_errors():
    def issues_of(**change):
        contract = copy.deepcopy(TRADE)
        contract["mechanisms"]["trade"].update(change)
        return [i.message for i in errors(contract)]

    both = [{"from": "$entity(ar)", "to": "$entity(bo)", "pay": "credits", "give": "steel", "amount": 1}]
    assert any("exactly one of `pay`" in m for m in issues_of(obligations=both))
    gold = [{"from": "$entity(ar)", "to": "$entity(bo)", "pay": "gold", "amount": 1}]
    assert any("'gold' is not a declared currency" in m for m in issues_of(obligations=gold))
    assert any("an enum issue needs `values`" in m for m in issues_of(issues={"flag": {"type": "enum"}}))
    assert any("issue 'note' cannot be used" in m for m in issues_of(issues={"note": {}}))
    assert any("is not a valid expression" in m for m in issues_of(value="$terms.price +"))


def test_agreements_actions_check_their_own_keys():
    def op(effect):
        return [(i.path, i.message, i.fix) for i in errors({**TRADE, "events": [{"do": [effect]}]})]

    accept = {"agreements": "trade", "action": "accept", "who": "$entity(ar)"}
    assert any(m == "`agreements.accept` needs `offer`" for _, m, _ in op(accept))
    assert any(m == "'answer' is not part of `agreements.accept`" for _, m, _ in op({**accept, "offer": "x", "answer": "yes"}))
    path, message, fix = op({"agreements": "trade", "action": "answer"})[0]
    assert path.endswith(".action") and message == "'answer' is not an action of trade (agreements negotiation)"
    assert fix == "actions: propose, counter, accept, reject, withdraw, fulfill"
    _, _, fix = op({"answer_offer": "trade", "offer": "x", "by": "$entity(ar)", "answer": "accept"})[0]
    assert fix.startswith('`answer_offer` is now the `agreements` op: {"agreements": "<mechanism>", "action": <action>')


# ---------------------------------------------------------------------------
# labor
# ---------------------------------------------------------------------------

WORK = {
    "name": "Work",
    "clock": {"rounds": 4},
    "types": {"person": {"agent": True, "props": {"skill": 1}}, "bakery": {"agent": True}},
    "entities": {"pat": {"type": "person", "name": "Pat", "props": {"skill": 3}},
                 "quinn": {"type": "person", "name": "Quinn", "props": {"skill": 5}},
                 "baker": {"type": "bakery", "name": "Baker", "props": {"cash": 25, "goods": {"flour": 5}}}},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": ["person", "bakery"], "currencies": {"cash": {}},
                  "taxes": {"income": {"rate": 0.1}}},
        "goods": {"kind": "economy", "mode": "inventory", "who": ["person", "bakery"], "items": {"flour": {}, "bread": {"value": 3}}},
        "jobs": {"kind": "agreements", "mode": "labor", "who": "person", "employers": "bakery", "currency": "cash", "wage_min": 5, "wage_max": 20,
                 "tax": "income", "inventory": "goods",
                 "firm": {"output": "bread", "per_worker": 2, "inputs": {"flour": 1}, "price": 3}}},
    "stages": [{"name": "day", "turns": "sequential", "max_actions": 3, "max_calls": 8}],
}


def test_labor_postings_applications_hiring_wages_output_and_quitting():
    assert errors(WORK) == []
    env = fg_env.load(WORK, seed=1)
    play = scripted({("baker", 1): [("jobs_post", {"title": "baker", "wage": 10, "openings": 1})],
                     ("pat", 2): [("jobs_apply", {"posting": "jobs_posting_1"})],
                     ("quinn", 2): [("jobs_apply", {"posting": "jobs_posting_1"})],
                     ("baker", 2): [("jobs_hire", {"application": "jobs_application_2"})],
                     ("quinn", 3): [("jobs_quit", {"job": "jobs_job_1"})]})
    env.run(play, rounds=2)
    assert all(r.ok for r in ok(play.results)), [r.text for r in ok(play.results)]
    assert blocked(env, "pat", "jobs_apply") == "there is no jobs_posting you can choose for posting right now"
    result = env.run(play, rounds=1)
    assert result.status != "failed", result.error
    assert play.reasons[("quinn", 3)] == "Baker hired you."
    assert env.entity("quinn")["props"]["cash"] == 9 and env.entity("baker")["props"]["cash"] == 15
    assert env.props["money_flows"] == {"income": {"cash": -1}}
    assert env.props["goods_flows"] == {"jobs production": {"flour": -2, "bread": 2}}
    assert env.props["jobs_stats"] == {"hires": 1, "quits": 1, "fires": 0, "wages": 10, "unpaid": 0, "produced": 2}
    assert env.entity("baker")["props"]["jobs_output"] == 0


@pytest.mark.parametrize("on_unpaid", ["quit", "owe"])
def test_rule_hiring_by_rank_and_unpaid_wages(on_unpaid):
    contract = copy.deepcopy(WORK)
    contract["mechanisms"]["jobs"].update(hiring="rule", rank="$it.skill", on_unpaid=on_unpaid)
    contract["entities"]["baker"]["props"]["cash"] = 5
    env = fg_env.load(contract, seed=1)
    assert "jobs_hire" not in env.contract.actions
    apply = [("jobs_apply", {"posting": "jobs_posting_1"})]
    env.run(scripted({("baker", 1): [("jobs_post", {"title": "baker", "wage": 10, "openings": 2})],
                      ("pat", 2): apply, ("quinn", 2): apply}), rounds=3)
    jobs = {j["props"]["worker"]: j["props"] for j in env.entities("jobs_job")}
    assert list(jobs) == ["quinn", "pat"]  # ranked by skill
    assert env.props["jobs_stats"]["unpaid"] == 2 and env.entity("baker")["props"]["goods"] == {"flour": 1, "bread": 4}
    if on_unpaid == "quit":
        assert {j["status"] for j in jobs.values()} == {"ended"} and jobs["pat"]["reason"] == "unpaid"
    else:
        assert {(j["status"], j["owed"]) for j in jobs.values()} == {("active", 10)}


# ---------------------------------------------------------------------------
# supply chain
# ---------------------------------------------------------------------------

CHAIN = {
    "name": "Chain",
    "clock": {"rounds": 6},
    "types": {"node": {"agent": True}},
    "entities": {"shop": {"type": "node", "name": "Shop"}, "plant": {"type": "node", "name": "Plant"}},
    "mechanisms": {
        "stock": {"kind": "economy", "mode": "inventory", "who": "node", "items": {"widget": {}}, "start": {"widget": 5}},
        "flow": {"kind": "economy", "mode": "supply_chain", "inventory": "stock", "item": "widget", "nodes": ["shop", "plant"],
                 "demand": "3 if $round < 3 else 6", "initial_flow": 3, "lead_time": 2, "production_delay": 1,
                 "holding_cost": 0.5, "backlog_cost": 2}},
    "stages": [{"name": "orders", "turns": "simultaneous"}],
}


def test_supply_chain_ships_backlogs_and_costs_with_goods_conserved_in_pipelines():
    assert errors(CHAIN) == []
    env = fg_env.load(CHAIN, seed=1)
    assert env.props["stock_supply"] == {"widget": 19}  # 5 + 5 in stock, 3 + 3 + 3 on the way
    result = env.run("idle", rounds=4)
    assert result.status != "failed", result.error
    shop, plant = env.entity("shop")["props"], env.entity("plant")["props"]
    assert (env.props["flow_sold"], shop["stock"], plant["stock"]) == (14, {}, {"widget": 5})
    assert (shop["flow_backlog"], shop["flow_peak_backlog"], shop["flow_cost"], plant["flow_cost"]) == (4, 4, 14, 10)
    assert env.props["stock_flows"] == {"sold": {"widget": -14}}


def test_supply_chain_orders_travel_up_and_production_enters_the_producer_pipeline():
    sequential = {**CHAIN, "stages": [{"name": "orders", "turns": "sequential", "max_actions": 2}]}
    env = fg_env.load(sequential, seed=1)
    play = scripted({("shop", 1): [("flow_order", {"qty": 4}), ("flow_order", {"qty": 1})],
                     ("plant", 1): [("flow_order", {"qty": 6})]})
    env.run(play, rounds=1)
    placed, twice, started = ok(play.results)
    assert placed.ok and started.ok and placed.text == "You ordered 4 units." and started.text == "You started a batch of 6 units."
    assert not twice.ok
    assert env.props["flow_pipes"]["plant"] == {"inbound": [6], "orders": [4]}
    assert env.props["stock_flows"] == {"sold": {"widget": -3}, "production": {"widget": 6}}
    env.run("idle", rounds=1)
    assert env.entity("plant")["props"]["flow_incoming"] == 4 and env.entity("plant")["props"]["flow_received"] == 6


# ---------------------------------------------------------------------------
# examples: conservation, determinism, resume
# ---------------------------------------------------------------------------


def _conserved_everywhere(env):
    uses = [name for name, use in env.contract.mechanisms.items() if use.get("mode") in ("ledger", "inventory")]
    assert uses
    return {name: compile_expr(f"$conserved('{name}')")(env.world.scope()) for name in uses}


@pytest.mark.parametrize("stem", sorted(LONG_RUNS))
def test_examples_conserve_value_over_200_random_agent_rounds(stem):
    env = fg_env.load(EXAMPLES / f"{stem}.json", inputs=LONG_RUNS[stem], seed=3)
    result = env.run("random")
    assert result.status == "completed" and result.rounds == 200, result.error
    assert result.stats["actions"] > 0
    assert set(_conserved_everywhere(env).values()) == {True}


@pytest.mark.parametrize("stem", sorted(LONG_RUNS))
def test_examples_are_deterministic_and_resume_identically(stem):
    path, inputs = EXAMPLES / f"{stem}.json", {**LONG_RUNS[stem]}
    straight = fg_env.load(path, inputs=inputs, seed=5).run("random", rounds=30).to_dict()
    assert fg_env.load(path, inputs=inputs, seed=5).run("random", rounds=30).to_dict() == straight
    env = fg_env.load(path, inputs=inputs, seed=5)
    env.run("random", rounds=12)
    restored = fg_env.Env.restore(path, json.loads(json.dumps(env.snapshot())))
    restored.run("random", rounds=18)
    assert restored.result().to_dict() == straight


@pytest.mark.parametrize("policy", [None, "policy:base_stock", "policy:passthrough"])
@pytest.mark.parametrize("arm", [None, "shared_demand"])
def test_native_beer_game_reproduces_the_hand_written_one(policy, arm):
    participants = {"tier": policy} if policy else None
    original = fg_env.load(EXAMPLES / "beer_game.json", seed=1, arm=arm).run(participants)
    native = fg_env.load(EXAMPLES / "beer_game_native.json", seed=1, arm=arm).run(participants)
    for key in ("total_cost", "bullwhip_ratio", "peak_backlog", "weeks_until_stable", "cost_by_tier", "peak_backlog_by_tier"):
        assert native.outputs[key] == original.outputs[key], key
    assert native.series["factory_order"] == original.series["factory_order"]


def test_guide_documents_every_economy_mode_function_and_op():
    mechanisms, effects, everything = fg_env.guide("mechanisms"), fg_env.guide("effects"), fg_env.guide()
    economy = fg_env.guide("economy")
    for mode in ("ledger", "inventory", "production", "supply_chain"):
        assert f"### `economy.{mode}`" in economy
    for action in ("pay", "mint", "burn", "lend", "repay", "give", "make", "use", "drop", "pickup", "start", "order"):
        assert f"- `{action}`" in economy
    assert "- `tick`" not in economy and "- `close`" not in economy  # generated bookkeeping stays out of the guide
    assert '- `economy`: {"economy": "<economy mechanism>", "action": ...}' in effects
    agreements = fg_env.guide("agreements")
    for mode in ("negotiation", "labor", "subscriptions", "bookings"):
        assert f"### `agreements.{mode}`" in agreements
    for action in ("propose", "counter", "accept", "reject", "withdraw", "fulfill", "hire", "quit", "fire", "subscribe",
                   "set_price", "book", "cancel"):
        assert f"- `{action}`" in agreements
    assert "- `payday`" not in agreements and "- `tick`" not in agreements
    assert '- `agreements`: {"agreements": "<agreements mechanism>", "action": ...}' in effects
    assert "| `economy` |" in mechanisms and "| `agreements` |" in mechanisms
    for fn in ("$has(", "$count_items(", "$net_worth(", "$conserved(", "$skill(", "$subscribed(", "$pipeline("):
        assert fn in everything
