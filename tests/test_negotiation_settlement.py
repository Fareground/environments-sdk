"""A signed deal settles itself: unique entities (a lot of phones) move to the buyer and on_sign effects run; a deal
that cannot settle is refused at acceptance, so one lot is never sold twice."""
import copy

import fg_env

LOTS = {
    "name": "Phone lots",
    "clock": {"rounds": 2},
    "types": {"desk": {"agent": True, "props": {}},
              "buyer": {"agent": True, "props": {"paid": 0}},
              "phone": {"props": {"owner": {"type": "text", "default": "desk"}, "grade": "A"}}},
    "entities": {"desk": {"type": "desk"}, "b1": {"type": "buyer"}, "b2": {"type": "buyer"}},
    "population": [{"type": "phone", "count": 5}],
    "world": {"revenue": 0},
    "mechanisms": {"wholesale": {
        "kind": "agreements", "mode": "negotiation", "who": ["desk", "buyer"], "once": False,
        "issues": {"units": {"type": "int", "min": 1, "max": 10}, "price": {"min": 50, "max": 500}},
        "transfers": [{"label": "phones", "items": "$filter(phone, $it.owner == $proposer.id)", "count": "$terms.units",
                       "to": "$acceptor"}],
        "on_sign": ["$world.revenue += $terms.units * $terms.price", "$acceptor.paid += $terms.units * $terms.price"]}},
    "stages": [{"name": "trade", "turns": "sequential", "max_actions": 3}],
}


def scripted(plan):
    results = []

    def participant(wake):
        for tool, args in plan.get((wake.entity_id, wake.round), []):
            results.append(wake.call(tool, args))
        if not wake.done:
            wake.end()

    participant.results = results
    return participant


def _errors(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


def test_a_signed_deal_hands_over_its_phones_and_runs_its_settlement_effects():
    assert _errors(LOTS) == []
    env = fg_env.load(LOTS, seed=1)
    play = scripted({("desk", 1): [("wholesale_propose", {"to": "b1", "units": 3, "price": 200})],
                     ("b1", 1): [("wholesale_accept", {"offer": "wholesale_offer_1"})]})
    result = env.run(play, rounds=1)
    assert result.status != "failed", result.error
    assert all(r.ok for r in play.results), [r.text for r in play.results]
    owners = [phone["props"]["owner"] for phone in env.entities("phone")]
    assert owners.count("b1") == 3 and owners.count("desk") == 2
    [deal] = env.entities("wholesale_deal")
    assert deal["props"]["items"] == [p["id"] for p in env.entities("phone") if p["props"]["owner"] == "b1"]
    assert env.props["revenue"] == 600 and env.entity("b1")["props"]["paid"] == 600


def test_a_deal_whose_phones_are_already_sold_is_refused_and_changes_nothing():
    env = fg_env.load(LOTS, seed=1)
    play = scripted({("desk", 1): [("wholesale_propose", {"to": "b1", "units": 3, "price": 200}),
                                   ("wholesale_propose", {"to": "b2", "units": 3, "price": 210})],
                     ("b1", 1): [("wholesale_accept", {"offer": "wholesale_offer_1"})],
                     ("b2", 1): [("wholesale_accept", {"offer": "wholesale_offer_2"})]})
    env.run(play, rounds=1)
    first, second, accepted, refused = play.results
    assert accepted.ok and not refused.ok
    assert "Only 2 of the 3 phones are available, so the deal cannot be signed." in refused.text
    assert len(env.entities("wholesale_deal")) == 1 and env.props["revenue"] == 600
    assert env.entity("wholesale_offer_2")["props"]["status"] == "open"  # still on the table, nothing half-done


def test_a_failing_on_sign_effect_refuses_the_acceptance_with_its_reason():
    contract = copy.deepcopy(LOTS)
    contract["mechanisms"]["wholesale"]["on_sign"] = [{"if": "$terms.price < 100",
                                                       "then": [{"fail": "Credit check failed."}]}]
    env = fg_env.load(contract, seed=1)
    play = scripted({("desk", 1): [("wholesale_propose", {"to": "b1", "units": 1, "price": 60})],
                     ("b1", 1): [("wholesale_accept", {"offer": "wholesale_offer_1"})]})
    env.run(play, rounds=1)
    assert not play.results[1].ok and "Credit check failed." in play.results[1].text
    assert env.entities("wholesale_deal") == [] and {p["props"]["owner"] for p in env.entities("phone")} == {"desk"}


def test_transfer_config_mistakes_are_reported_at_their_path():
    contract = copy.deepcopy(LOTS)
    contract["mechanisms"]["wholesale"]["transfers"][0]["field"] = "not a name"
    [issue] = _errors(contract)
    assert issue.path.endswith("transfers[0].field") and "cannot be a property name" in issue.message
    contract = copy.deepcopy(LOTS)
    contract["mechanisms"]["wholesale"]["transfers"][0]["items"] = "$filter(phone,"
    [issue] = _errors(contract)
    assert issue.path.endswith("transfers[0].items") and "syntax error" in issue.message


def test_the_negotiation_guide_describes_transfers_and_on_sign():
    page = fg_env.guide("agreements.negotiation")
    assert "transfers" in page and "on_sign" in page
