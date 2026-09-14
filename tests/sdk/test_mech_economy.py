"""Economy mechanisms: inventory, ledger, production, subscriptions, bookings, negotiation, labor
and supply chains — value is conserved, tools list only valid choices, runs are deterministic."""
import copy

import pytest

import fg_env


def scripted(plan):
    """A participant playing ``plan``: {(entity_id, round): [(tool, args), ...]}; results are kept."""
    results = []

    def participant(wake):
        for tool, args in plan.get((wake.entity_id, wake.round), []):
            results.append((wake.entity_id, tool, wake.call(tool, args)))
        if not wake.done:
            wake.end()

    participant.results = results
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
    "mechanisms": {"goods": {"kind": "inventory", "holders": "person", "capacity": 4,
                             "items": {"bread": {"value": 2, "on_consume": ["$actor.hunger -= $qty"]},
                                       "apple": {"value": 1},
                                       "sword": {"unique": True, "value": 30, "props": {"sharp": 1}}},
                             "tools": ["give", "consume", "drop", "pickup"]}},
    "stages": [{"name": "act", "turns": "sequential", "max_actions": 4, "max_calls": 10}],
}


def test_inventory_tools_offer_only_goods_you_hold_and_moves_conserve_them():
    env = fg_env.load(GOODS, seed=1)
    give = tool(env, "ana", "goods_give")
    assert give["item"]["enum"] == ["bread", "apple"]
    assert give["to"]["enum"] == ["ben"]
    assert tool(env, "ben", "goods_consume")["item"].get("enum") is None or "bread" not in tool(env, "ben", "goods_consume")["item"]["enum"]
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
    contract["events"] = [{"at": 1, "do": [{"make_items": "sword", "to": "$entity(ben)", "source": "forge",
                                            "props": {"sharp": 3}}]}]
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
    "mechanisms": {"food": {"kind": "inventory", "holders": "person", "items": {"bread": {"shelf_life": 2}},
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
    "mechanisms": {"money": {"kind": "ledger", "holders": "person", "currencies": {"cash": {"start": 10}}},
                   "goods": {"kind": "inventory", "holders": "person", "items": {"bread": {}}}},
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
    ground["mechanisms"]["goods"]["tools"] = ["drop"]
    assert any("drop and pickup need a declared space" in i.message for i in errors(ground))
    unique = copy.deepcopy(CHEAT)
    unique["mechanisms"]["goods"]["items"] = {"ring": {"unique": True, "consumable": True}}
    assert any("cannot be consumable" in i.message for i in errors(unique))
    holders = copy.deepcopy(CHEAT)
    holders["mechanisms"]["goods"]["holders"] = "robot"
    assert any(i.path == "mechanisms.goods.holders" for i in errors(holders))


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
        "kind": "ledger", "holders": ["person", "bank"], "currencies": {"cash": {"start": 50, "credit": 10}},
        "sources": {"ubi": {"to": "person", "amount": 5, "every": 2, "start": 2}},
        "taxes": {"vat": {"rate": 0.1, "on": "payer"}, "levy": {"rate": 0.2}},
        "loans": {"lenders": "bank", "borrowers": "person", "rate_min": 0.1, "rate_max": 0.1, "max_term": 3,
                  "on_default": ["$borrower.defaults += 1"]},
        "tools": ["pay"]}},
    "actions": {"shop": {"by": "person", "params": {"amount": "number"},
                         "do": [{"pay": "cash", "from": "$actor", "to": "$entity(vault)", "amount": "$params.amount", "tax": "vat"}]},
                "earn": {"by": "person", "do": [{"pay": "cash", "from": "$entity(vault)", "to": "$actor", "amount": 10, "tax": "levy"}]}},
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


def test_net_worth_counts_money_goods_and_loans():
    contract = copy.deepcopy(BANKING)
    contract["mechanisms"]["goods"] = {"kind": "inventory", "holders": "person", "items": {"bread": {"value": 2}},
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
        "goods": {"kind": "inventory", "holders": "villager", "capacity": 12,
                  "items": {"grain": {}, "flour": {}, "wood": {}, "axe": {"unique": True}}},
        "money": {"kind": "ledger", "holders": "villager", "currencies": {"coin": {"start": 3}}},
        "craft": {"kind": "production", "producers": "villager", "inventory": "goods",
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
    play = scripted({("ivy", 1): [("craft_start", {"recipe": "chop"}), ("craft_start", {"recipe": "mill", "times": 3}),
                                  ("craft_start", {"recipe": "mill", "times": 2}), ("craft_start", {"recipe": "mill"})],
                     ("ivy", 2): [("craft_start", {"recipe": "fine_mill"})]})
    result = env.run(play, rounds=2)
    assert result.status != "failed", result.error
    chop, too_many, milling, busy, fine = ok(play.results)
    assert chop.ok and "done at once" in chop.text
    assert not too_many.ok and "times must be at most 2" in too_many.text
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
    contract["events"] = [{"at": 2, "do": [{"make_items": "wood", "to": "$entity(ivy)", "qty": 4, "source": "gift"}]},
                          {"at": 3, "do": [{"use_items": "wood", "from": "$entity(ivy)", "qty": 4, "sink": "fire"}]}]
    env = fg_env.load(contract, seed=1)
    result = env.run(scripted({("ivy", 1): [("craft_start", {"recipe": "mill", "times": 2})]}), rounds=2)
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
    assert any("'stock' is not a declared inventory mechanism" in i.message for i in errors(missing))
