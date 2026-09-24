"""The bundled engines behave realistically under their default inputs, and their outputs move the way the inputs
that should drive them say: price moves demand, persuasion moves the vote spread, trust moves reach, evidence moves the
verdict. Each direction is read over several seeds and with room for luck, so a test states the effect, not a number."""
import os
import statistics

import pytest

import fg_env

SEEDS = range(8)


def run(engine_id, participants=None, *, seed=0, inputs=None, rounds=None):
    result = fg_env.engines.load(engine_id, inputs=inputs, seed=seed).run(participants, rounds=rounds)
    assert result.error is None, result.error
    return result


def mean(engine_id, output, inputs=None, seeds=SEEDS, **kwargs):
    return statistics.fmean(run(engine_id, seed=seed, inputs=inputs, **kwargs).outputs[output] for seed in seeds)


def default(engine_id, name):
    return fg_env.engines.get(engine_id).source()["inputs"][name]["default"]


# ---------------------------------------------------------------------------------------------------------- contest

def _contestants(count, skill=lambda i: 0.5):
    return [{"id": f"c{i}", "name": f"C{i}", "approach": "", "skill": skill(i)} for i in range(count)]


def test_a_large_contest_still_names_its_winner():
    result = run("contest", inputs={"participants": _contestants(100), "rounds": 1})
    assert result.degraded == ["host_fallback"] and result.output_issues == []  # no judge bound: stand-in scores
    assert result.outputs["winner"] is not None and result.outputs["contestants"] == 100


def test_skill_moves_who_wins_a_contest():
    field = {"participants": _contestants(6, skill=lambda i: 0.2 + 0.1 * i)}
    winners = [run("contest", seed=seed, inputs=field).outputs["winner"] for seed in range(20)]
    assert winners.count("C5") > winners.count("C0")  # the most skilled wins more often than the least


@pytest.mark.parametrize("count", [0, 1])
def test_a_contest_needs_two_contestants(count):
    with pytest.raises(fg_env.InvariantViolation, match="at least two contestants"):
        run("contest", inputs={"participants": _contestants(count)})


# ---------------------------------------------------------------------------------------------------------- council

def test_a_one_person_panel_is_refused_with_the_fix():
    panel = default("council", "panel")[:1]
    with pytest.raises(fg_env.InvariantViolation, match="at least two panelists"):
        run("council", inputs={"panel": panel})


def test_panel_priors_move_the_council_forecast():
    panel = default("council", "panel")
    low = [{**p, "prior": max(0, p["prior"] - 25)} for p in panel]
    high = [{**p, "prior": min(100, p["prior"] + 25)} for p in panel]
    assert mean("council", "final_mean", {"panel": low}) + 20 < mean("council", "final_mean", {"panel": high})


# ---------------------------------------------------------------------------------------------------------- network

def test_the_network_seeds_are_an_input_checked_against_the_people():
    people = [{"id": f"q{i}", "name": f"Q{i}", "group": "A", "receptivity": 0.5} for i in range(3)]
    with pytest.raises(fg_env.InvariantViolation, match="seeds"):
        run("network", inputs={"participants": people, "ties": []})  # the default seed p1 is not among them
    result = run("network", inputs={"participants": people, "ties": [], "seeds": ["q2"]})
    assert result.outputs["adopted"] == 1 and result.outputs["adoption_rate"] == pytest.approx(1 / 3)
    assert run("network", inputs={"participants": people, "ties": [], "seeds": []}).outputs["adopted"] == 0


def test_more_seeds_reach_more_people():
    assert mean("network", "adoption_rate", {"seeds": ["p1"]}) < mean("network", "adoption_rate", {"seeds": ["p1", "p4"]})


def test_trust_above_one_is_refused_at_input():
    ties = [{**tie, "value": 1.4} for tie in default("network", "ties")]
    with pytest.raises(fg_env.InputError, match="value"):
        run("network", inputs={"ties": ties})


def test_trust_moves_reach():
    ties = default("network", "ties")
    weak = [{**tie, "value": tie["value"] / 3} for tie in ties]
    assert mean("network", "reached", {"ties": weak}) < mean("network", "reached", {"ties": ties})


# ------------------------------------------------------------------------------------------ legislature, deliberation

def _members(rows):
    """Legislature members from (party, stance) pairs; the first moves the measure."""
    return [{"id": f"l{i}", "name": f"M{i}", "party": party, "stance": stance, "sponsor": i == 0}
            for i, (party, stance) in enumerate(rows)]


def _people(stances):
    return [{"id": f"m{i}", "name": f"M{i}", "stance": stance, "perspective": "p"} for i, stance in enumerate(stances)]


def _body(engine_id, stances):
    """The same people as either engine's input table (one party, so the legislature's party cue is neutral)."""
    if engine_id == "legislature":
        return {"members": _members([("A", s) for s in stances])}
    return {"participants": _people(stances)}


def _votes(engine_id, stances, seeds=range(20), **inputs):
    return [run(engine_id, seed=seed, inputs={**_body(engine_id, stances), **inputs}).outputs for seed in seeds]


def _yes_votes(engine_id, stances, seeds=range(20), **inputs):
    return [outputs["yes"] for outputs in _votes(engine_id, stances, seeds, **inputs)]


#: Three strong supporters (the mover first) against six mild opponents: most of the room is against the measure.
_LOPSIDED = [0.9, 0.9, 0.9] + [-0.3] * 6


@pytest.mark.parametrize("engine_id", ["legislature", "deliberation"])
def test_the_order_of_the_table_does_not_decide_the_vote(engine_id):
    supporters_first = _yes_votes(engine_id, _LOPSIDED)
    opponents_first = _yes_votes(engine_id, _LOPSIDED[:1] + _LOPSIDED[:0:-1])  # same people, mover still first
    assert abs(statistics.fmean(supporters_first) - statistics.fmean(opponents_first)) < 1.5
    assert statistics.fmean(supporters_first) < 4.5  # the mild majority is not talked round by who spoke first


@pytest.mark.parametrize("engine_id", ["legislature", "deliberation"])
def test_a_divided_body_does_not_collapse_into_unanimity(engine_id):
    stances = [0.8, -0.6, 0.4, -0.3, 0.2, -0.5, 0.6, -0.1, 0.3, -0.7] * 3
    yes = _yes_votes(engine_id, stances, seeds=range(4))
    assert all(5 < y < 25 for y in yes), yes  # members stay anchored to where they started


@pytest.mark.parametrize("engine_id", ["legislature", "deliberation"])
def test_persuasion_moves_the_vote_spread(engine_id):
    """Persuasive debate pulls members toward the room, so the more they listen the more lopsided the vote."""
    stances = [0.7, 0.5, 0.3, 0.1, 0.05, -0.05, -0.15, -0.6]

    def margin(**inputs):
        return statistics.fmean(abs(o["yes"] - o["no"]) for o in _votes(engine_id, stances, **inputs))

    assert margin(persuasion=0) < margin() < margin(persuasion=0.5)


def test_the_party_cue_pulls_dissenters_toward_their_party():
    rows = [("A", 0.8), ("A", 0.8), ("A", 0.8), ("A", -0.1), ("A", -0.1)] + [("B", -0.5)] * 4
    loyal = statistics.fmean(run("legislature", seed=s, inputs={"members": _members(rows), "party_loyalty": 1})
                             .outputs["yes"] for s in range(20))
    independent = statistics.fmean(run("legislature", seed=s, inputs={"members": _members(rows), "party_loyalty": 0})
                                   .outputs["yes"] for s in range(20))
    assert loyal > independent + 0.5  # party A's dissenters listen to their own party


@pytest.mark.parametrize("size", [20, 40])
def test_the_chair_calls_the_vote_once_every_member_has_spoken(size):
    result = run("legislature", inputs={"members": _members([("A", 0.5)] * size)})
    assert result.outputs["decided"] and result.outputs["speeches"] == size


# ------------------------------------------------------------------------------------------------------ negotiation

def _parties(**changes):
    """The default two parties, with per-party changes: _parties(party_b={"reservation": 40})."""
    return [{**party, **changes.get(party["id"], {})} for party in default("negotiation", "participants")]


def test_coded_negotiators_trade_off_the_issues_they_care_least_about():
    """Party A cares most about scope, party B about amount and timing: conceding the issues each cares least about
    gives both a deal worth far more than meeting halfway on every issue (50, 50, 6 is worth 13.8 to the two together)."""
    deals = [run("negotiation", seed=seed).outputs for seed in SEEDS]
    assert all(o["deal_signed"] and min(o["surplus"].values()) > 0 for o in deals)
    assert all(sum(o["surplus"].values()) > 30 for o in deals), [o["surplus"] for o in deals]
    assert all(o["terms"]["scope"] > 80 and o["terms"]["amount"] > 50 for o in deals), [o["terms"] for o in deals]


def test_a_mutually_acceptable_deal_is_found_when_one_exists():
    # (amount 70, scope 70, timing 12) is worth 18.6 to A and 41 to B, so a walk-away of 40 still leaves a deal
    demanding = {"participants": _parties(party_b={"reservation": 40})}
    assert all(run("negotiation", seed=seed, inputs=demanding).outputs["deal_signed"] for seed in SEEDS)


def test_a_higher_walk_away_value_wins_that_party_more_of_the_deal():
    def surplus_of_a(reservation_b):
        parties = {"participants": _parties(party_b={"reservation": reservation_b})}
        return statistics.fmean(run("negotiation", seed=seed, inputs=parties).outputs["surplus"]["Party A"]
                                for seed in range(4))
    assert surplus_of_a(40) < surplus_of_a(17)


@pytest.mark.parametrize("changes, message", [
    ({"party_a": {"counterparty": "nobody"}}, "counterparty"),
    ({"party_a": {"initiator": False}}, "initiator"),
])
def test_a_negotiation_that_cannot_start_is_refused_with_the_fix(changes, message):
    with pytest.raises(fg_env.InvariantViolation, match=message):
        run("negotiation", inputs={"participants": _parties(**changes)})


# ---------------------------------------------------------------------------------------------------------- dispute

def _exhibits(strength):
    """The default exhibits with each one's strength replaced by strength(row)."""
    return [{**row, "strength": strength(row)} for row in default("dispute", "exhibits")]


def test_the_evidence_moves_the_verdict():
    def liable_share(exhibits):
        verdicts = [run("dispute", seed=seed, inputs={"exhibits": exhibits}).outputs["verdict"] for seed in range(12)]
        return verdicts.count("liable") / len(verdicts)

    as_given = liable_share(default("dispute", "exhibits"))
    weak_plaintiff = liable_share(_exhibits(lambda row: row["strength"] / 3 if row["side"] == "plaintiff" else row["strength"]))
    weak_defense = liable_share(_exhibits(lambda row: row["strength"] / 3 if row["side"] == "defense" else row["strength"]))
    assert weak_plaintiff < as_given - 0.4 and weak_plaintiff < weak_defense


def test_juror_leanings_move_the_verdict():
    jurors = default("dispute", "jurors")
    for_defense = [{**juror, "bias": -0.8} for juror in jurors]
    liable = [run("dispute", seed=seed, inputs=inputs).outputs["verdict"] == "liable"
              for seed in range(12) for inputs in ({}, {"jurors": for_defense})]
    assert sum(liable[1::2]) < sum(liable[0::2])


def test_coded_counsel_objections_sometimes_keep_evidence_out_by_default():
    runs = [run("dispute", seed=seed).outputs for seed in range(30)]
    kept_out = [o for o in runs if o["excluded_exhibits"]]
    assert 0 < len(kept_out) < len(runs)  # counsel sometimes risk a flawed exhibit, and the objection lands
    assert all(o["objection_success_rate"] == 1 for o in runs if o["objections_made"])  # they object on the real flaw


def test_the_case_is_an_input():
    env = fg_env.engines.load("dispute", inputs={"plaintiff": "Acme Bakery", "defendant": "Zenith Mills",
                                                 "case": "Zenith delivered spoiled flour."})
    brief = env.preview("juror_1")["brief"]
    assert "Acme Bakery" in brief and "Zenith delivered spoiled flour." in brief and "Harbor" not in brief


def test_a_jury_must_be_able_to_reach_the_verdict_threshold():
    with pytest.raises(fg_env.InvariantViolation, match="votes_required"):
        run("dispute", inputs={"jurors": default("dispute", "jurors")[:3]})


# ----------------------------------------------------------------------------------------------------------- market

def _cafes(**changes):
    """The default cafés with each column change applied as a function of the row: _cafes(price=lambda r: ...)."""
    return [{**row, **{k: f(row) for k, f in changes.items()}} for row in default("market", "cafes")]


def test_price_moves_total_demand():
    """Households weigh the best café against making coffee at home, so dearer coffee sells fewer cups (a week, before
    any café reprices)."""
    def cups(scale):
        return mean("market", "cups_sold", {"days": 7, "sample_size": 80,
                                            "cafes": _cafes(price=lambda row: round(row["price"] * scale, 2))},
                    seeds=range(3))
    assert cups(0.6) > cups(1) > cups(1.6)


def test_a_cafe_reprices_toward_more_profit_not_toward_a_full_house():
    """A lone café with room to spare and a price barely above cost raises it: demand for coffee is inelastic, so a
    fill-seeking rule would cut instead."""
    alone = _cafes(price=lambda row: 2 * row["unit_cost"], capacity=lambda row: 100000)[:1]
    env = fg_env.engines.load("market", inputs={"cafes": alone, "days": 36, "sample_size": 80}, seed=1)
    env.run()
    assert env.entities("cafe")[0]["props"]["price"] > 1.1 * 2 * alone[0]["unit_cost"]  # four weekly reviews


def test_competing_cafes_keep_their_margins_over_a_long_run():
    env = fg_env.engines.load("market", inputs={"days": 64, "sample_size": 80}, seed=2)
    env.run()
    assert all(c["props"]["price"] > 2.2 * c["props"]["unit_cost"] for c in env.entities("cafe") if c["props"]["open"])


def test_the_smallest_sample_does_not_invent_crowds():
    assert all(run("market", seed=seed, inputs={"sample_size": 80, "days": 14}).outputs["turned_away_total"] == 0
               for seed in range(3))
    with pytest.raises(fg_env.InputError, match="sample_size"):
        run("market", inputs={"sample_size": 10})


def test_the_cafes_are_an_input():
    renamed = _cafes(name=lambda row: f"Shop {row['id']}")
    shares = run("market", inputs={"cafes": renamed, "days": 7, "sample_size": 80}).outputs["market_shares"]
    assert {name for name, _ in shares} == {f"Shop {row['id']}" for row in renamed}


# --------------------------------------------------------------------------------------------------------- exchange

_SMALL_EXCHANGE = {"bars": 6, "participants": 40}


def test_volatility_widens_the_exchange_spread_and_drift_moves_its_price():
    def avg(output, **inputs):
        return mean("exchange", output, {**_SMALL_EXCHANGE, **inputs}, seeds=range(2))
    assert avg("spread_bps_avg", volatility_scale=0.2) < avg("spread_bps_avg", volatility_scale=3)
    assert avg("return_pct", drift_pct_per_bar=-3) < avg("return_pct", drift_pct_per_bar=3)


@pytest.mark.skipif(not os.environ.get("FG_ENV_SLOW"), reason="six full sessions: FG_ENV_SLOW=1")
def test_the_exchanges_market_makers_earn_their_spread_as_a_class():
    pnl = [run("exchange", seed=seed).outputs["pnl_by_kind"]["market_maker"] for seed in range(1, 7)]
    assert sum(p > 0 for p in pnl) >= 5, pnl
