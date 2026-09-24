"""The bundled engines report what actually happened: no fabricated winners, ties as ties, every player paid,
advertised inputs that move outcomes, and verdicts that agree with the evidence they are measured against."""
import json
import statistics
from importlib.resources import files
from pathlib import Path

import pytest

import fg_env
from fg_env.host.stubs import StubEvaluator

pytestmark = pytest.mark.slow  # statistical or engine-behaviour: `make test-fast` leaves it out

SEEDS = range(6)


def run(engine_id, participants=None, *, seed=0, inputs=None):
    return fg_env.engines.load(engine_id, inputs=inputs, seed=seed).run(participants)


def test_contest_without_a_judge_is_decided_by_skill_and_luck_and_says_so():
    result = run("contest")
    assert [d["code"] for d in result.diagnostics] == ["host_fallback"]  # the rubric scores were a stand-in
    assert result.degraded == ["host_fallback"] and not result.ok
    assert "judge" in result.diagnostics[0]["message"]
    assert result.outputs["judged"] is False and result.outputs["winning_score"] is None  # no stand-in score reported
    winners = [run("contest", seed=seed).outputs["winner"] for seed in range(20)]
    assert {"Contestant 1", "Contestant 2"} <= set(winners)  # luck matters...
    assert winners.count("Contestant 1") > winners.count("Contestant 2")  # ...but the more skilled wins more often

    even = [{"id": k, "name": k, "approach": "", "skill": 0.5} for k in ("c1", "c2")]
    assert run("contest", inputs={"participants": even, "luck": 0}).outputs["winner"] is None  # a true tie stays one


def test_contest_knows_it_was_judged_from_the_verdicts_not_the_host_tape():
    assert "host_tape" not in json.dumps(fg_env.engines.get("contest").source())
    path = Path(str(files("fg_env.engines").joinpath(fg_env.engines.get("contest").path)))
    judged = fg_env.host.load(path, hosts={"judge": StubEvaluator()}, seed=1).run()
    assert judged.outputs["judged"] is True and judged.outputs["winning_score"] is not None
    assert run("contest").outputs["judged"] is False


def _players(**strategies):
    return [{"id": k, "name": k.upper(), "strategy": v} for k, v in strategies.items()]


def test_strategy_pays_every_pair_of_players_and_reports_ties():
    three = _players(a="always_cooperate", b="tit_for_tat", c="grim_trigger")
    result = run("strategy", inputs={"participants": three, "rounds": 2, "mistakes": 0})
    # round-robin: each player meets two others per round, 2 rounds x 2 opponents x mutual_cooperate (3)
    assert result.outputs["top_score"] == 12
    assert result.outputs["winner"] is None  # a three-way tie

    mixed = _players(a="always_cooperate", b="always_compete", c="always_cooperate")
    result = run("strategy", inputs={"participants": mixed, "rounds": 1, "mistakes": 0})
    assert result.outputs["winner"] == "B" and result.outputs["top_score"] == 10  # the bonus against each cooperator


def test_classic_strategies_react_to_the_history():
    def choices(env, player):
        return [e["data"]["params"]["choice"] for e in env.result().events
                if e["kind"] == "action" and e.get("actor") == player]

    env = fg_env.engines.load("strategy", inputs={"participants": _players(t="tit_for_tat", d="always_compete"),
                                                  "rounds": 3, "mistakes": 0})
    env.run()
    assert choices(env, "t") == ["cooperate", "compete", "compete"]  # opens nice, then mirrors the defector

    env = fg_env.engines.load("strategy", inputs={"participants": _players(g="grim_trigger", r="random"),
                                                  "rounds": 12, "mistakes": 0}, seed=1)
    env.run()
    grim, other = choices(env, "g"), choices(env, "r")
    betrayed = other.index("compete")
    assert grim == ["cooperate"] * (betrayed + 1) + ["compete"] * (11 - betrayed)  # never forgives


def test_mistakes_break_a_cooperative_equilibrium():
    nice = {"participants": _players(a="tit_for_tat", b="tit_for_tat"), "rounds": 10}
    assert ({run("strategy", seed=seed, inputs={**nice, "mistakes": 0}).outputs["cooperation_rate"] for seed in SEEDS}
            == {1.0})
    assert statistics.fmean(run("strategy", seed=seed, inputs={**nice, "mistakes": 0.3}).outputs["cooperation_rate"]
                            for seed in SEEDS) < 0.9


def _adoption(inputs):
    return statistics.fmean(run("network", seed=seed, inputs=inputs).outputs["adoption_rate"] for seed in range(12))


def _rejecter(wake):
    if any(tool.name == "reject" for tool in wake.tools):
        wake.call("reject")
    if not wake.done:
        wake.end()


def _sharer(wake):
    share = next((tool for tool in wake.tools if tool.name == "share"), None)
    if share is not None:
        wake.call("share", {"contact": share.input_schema["properties"]["contact"]["enum"][0]})
    if not wake.done:
        wake.end()


def test_network_people_can_act_on_the_idea():
    assert _adoption({}) > 0
    turned_down = statistics.fmean(run("network", _rejecter, seed=seed).outputs["adoption_rate"] for seed in range(12))
    assert turned_down < _adoption({})  # whoever hears of it and turns it down never passes it on
    shared = statistics.fmean(run("network", _sharer, seed=seed).outputs["adoption_rate"] for seed in range(12))
    assert shared > _adoption({})  # recommending on top of word of mouth spreads it further


def test_network_seed_stays_committed_so_the_idea_never_dies_at_its_source():
    assert all(run("network", _rejecter, seed=seed).outputs["adopted"] >= 1 for seed in range(12))
    assert all(run("network", "random", seed=seed).outputs["adopted"] >= 1 for seed in range(12))


def test_network_trust_and_time_drive_adoption():
    ties = fg_env.engines.get("network").source()["inputs"]["ties"]["default"]
    weak = [{**tie, "value": 0.05} for tie in ties]
    assert _adoption({"ties": weak}) < _adoption({"ties": ties}) - 0.1
    assert _adoption({"rounds": 3}) < _adoption({"rounds": 20}) - 0.1


def test_dispute_verdict_mostly_follows_the_evidence_it_is_measured_against():
    verdicts = [run("dispute", seed=seed).outputs for seed in range(20)]
    decided = [o for o in verdicts if o["verdict"] != "hung"]
    agree = [o for o in decided if (o["verdict"] == "liable") == (o["net_admitted_strength"] > 0)]
    assert len(agree) >= 0.8 * len(decided)  # jurors' leanings and persuasion can still carry a close case
    assert {"liable", "not_liable"} <= {o["verdict"] for o in verdicts}  # the starter is not locked to one verdict


def test_coded_counsel_lead_with_their_strongest_admissible_exhibits_so_verdicts_track_the_merits():
    env = fg_env.engines.load("dispute")
    env.run()
    first_day = {e["props"]["side"]: e["id"] for e in env.entities("exhibit") if e["props"]["day"] == 1}
    assert first_day == {"plaintiff": "P1", "defense": "D1"}  # each side's strongest clean exhibit, not a hearsay one
    for rounds in (1, 2, 3, 4):  # the plaintiff holds the stronger admissible case however many days of evidence
        verdicts = [run("dispute", seed=seed, inputs={"evidence_rounds": rounds}).outputs["verdict"]
                    for seed in range(20)]
        assert verdicts.count("liable") > 2 * verdicts.count("not_liable"), (rounds, verdicts)


def test_jury_room_speeches_move_the_jury():
    close = {"evidence_rounds": 1}  # one exhibit a side: a close case the jury room has to settle
    unmoved = [run("dispute", seed=seed, inputs={**close, "persuasion": 0}).outputs for seed in range(20)]
    # nobody is persuaded, so every re-ballot repeats the first: a jury that needs a second ballot hangs
    assert all(o["verdict"] == "hung" for o in unmoved if o["ballots_taken"] > 1)
    assert any(o["verdict"] == "hung" for o in unmoved)
    moved = [run("dispute", seed=seed, inputs=close).outputs for seed in range(20)]
    assert any(o["verdict"] != "hung" and o["ballots_taken"] > 1 for o in moved)  # deliberation broke a deadlock


def test_council_scores_forecasts_against_the_outcome_and_measures_consensus_by_spread():
    unresolved = run("council").outputs
    assert unresolved["final_brier"] is None

    env = fg_env.engines.load("council", inputs={"outcome": True})
    resolved = env.run().outputs
    finals = [panelist.properties["final"] / 100 for panelist in env.world.entities_of("panelist")]
    assert resolved["final_brier"] == pytest.approx(statistics.fmean((1 - final) ** 2 for final in finals))

    runs = [run("council", seed=seed).outputs for seed in range(12)]
    assert len({o["initial_mean"] for o in runs}) > 1  # each panelist reads the briefing with private noise
    assert all(o["final_stdev"] < o["initial_stdev"] for o in runs)  # hearing each other narrows the spread...
    assert all(o["final_range"] > 0 for o in runs)  # ...part of the way: nobody is averaged into agreement
    assert run("council", inputs={"consensus_within": 100}).outputs["consensus_reached"] is True

    anchored = run("council", "policy:anchored").outputs
    assert anchored["final_range"] > 10 and anchored["consensus_reached"] is False  # all ready, far apart


def test_council_panel_and_question_are_inputs():
    panel = [{"id": f"x{i}", "name": f"Expert {i}", "expertise": "e", "lens": "l", "prior": prior, "private_info": ""}
             for i, prior in enumerate((20, 80, 50))]
    env = fg_env.engines.load("council", inputs={"panel": panel, "question": "Will the bridge open on time?"})
    assert "3 experts" in env.preview("x0")["brief"] and "Will the bridge open on time?" in env.preview("x0")["brief"]
    assert env.run().outputs["messages"] == 3


def test_negotiation_never_binds_a_party_below_its_walk_away():
    deals = 0
    for seed in range(20):
        outputs = run("negotiation", "random", seed=seed).outputs
        if outputs["deal_signed"]:
            deals += 1
            assert min(outputs["surplus"].values()) >= 0, outputs
    assert deals  # random parties still find acceptable deals


def test_coded_negotiators_concede_toward_a_deal_before_the_deadline():
    runs = [run("negotiation", seed=seed).outputs for seed in range(10)]
    assert all(o["deal_signed"] and o["counteroffers"] > 0 for o in runs)  # they bargain, not sign the opener
    assert all(min(o["surplus"].values()) >= 0 for o in runs)
    assert len({tuple(sorted(o["terms"].items())) for o in runs}) > 1  # the terms depend on the run
    shortest = fg_env.engines.get("negotiation").source()["inputs"]["deadline"]["min"]
    assert all(run("negotiation", seed=seed, inputs={"deadline": shortest}).outputs["deal_signed"]
               for seed in range(10))
    later = [run("negotiation", seed=seed, inputs={"deadline": 20}).outputs for seed in range(10)]
    assert statistics.fmean(o["agreement_round"] for o in later) > statistics.fmean(o["agreement_round"] for o in runs)
    parties = fg_env.engines.get("negotiation").source()["inputs"]["participants"]["default"]
    greedy = [{**parties[0], "reservation": 1000}, parties[1]]  # no terms are worth that much to party A
    assert run("negotiation", inputs={"participants": greedy}).outputs["deal_signed"] is False


def test_negotiation_refuses_more_than_two_parties():
    three = [{"id": f"p{i}", "name": f"P{i}", "role": "", "counterparty": "p0", "initiator": i == 0,
              "amount_weight": 0.1, "scope_weight": 0.1, "timing_weight": 0.1, "reservation": 0} for i in range(3)]
    with pytest.raises(fg_env.InvariantViolation, match="exactly two parties"):
        run("negotiation", inputs={"participants": three})


def test_strategy_refuses_a_game_of_one():
    with pytest.raises(fg_env.InvariantViolation, match="at least two"):
        run("strategy", inputs={"participants": _players(a="tit_for_tat")})


@pytest.mark.parametrize("engine_id", ["legislature", "deliberation"])
def test_a_body_that_never_votes_records_the_status_quo(engine_id):
    assert run(engine_id, "idle").outputs["outcome"] == "status_quo"


def test_an_opposed_group_can_bring_the_question_to_a_vote_and_reject_it():
    opposed = [{"id": "m1", "name": "M1", "stance": -0.7, "perspective": "x"},
               {"id": "m2", "name": "M2", "stance": -0.2, "perspective": "y"}]
    assert run("deliberation", inputs={"participants": opposed}).outputs["outcome"] == "rejected"


def test_the_chair_calls_the_question_once_the_chamber_could_be_heard():
    members = [{"id": f"l{i}", "name": f"M{i}", "party": "A", "stance": 0.5, "sponsor": i == 0} for i in range(6)]
    assert run("legislature", inputs={"members": members}).outputs["speeches"] >= 6


@pytest.mark.parametrize("engine_id", ["legislature", "deliberation"])
def test_debate_moves_votes_only_when_speeches_persuade(engine_id):
    unmoved = {tuple(sorted(run(engine_id, seed=seed, inputs={"persuasion": 0}).outputs.items())) for seed in SEEDS}
    assert len(unmoved) == 1  # nobody changes their mind: every seed votes the starting stances
    assert {run(engine_id, seed=seed).outputs["outcome"] for seed in range(10)} == {"passed", "rejected"}


def _match(seed, **inputs):
    env = fg_env.engines.load("matching", inputs=inputs, seed=seed)
    env.run()
    return env


def _blocking_pairs(env):
    """Applicant/selector pairs who both rank each other above what they got: a stable match has none."""
    people = {e["id"]: e["props"] for e in env.entities()}
    pairs = []
    for a, applicant in people.items():
        if "placement_match" not in applicant:
            continue
        prefs, current = applicant["placement_prefs"], applicant["placement_match"]
        for s in prefs[:prefs.index(current) if current else len(prefs)]:
            selector = people[s]
            ranking, held = selector["placement_prefs"], selector["placement_matches"]
            if a in ranking and (len(held) < selector["capacity"]
                                 or ranking.index(a) < max(ranking.index(h) for h in held)):
                pairs.append((a, s))
    return pairs


def test_matching_is_stable_within_capacity_and_varies_with_taste():
    assignments = set()
    for seed in range(10):
        env = _match(seed)
        assert _blocking_pairs(env) == []
        assert all(len(s["props"]["placement_matches"]) <= s["props"]["capacity"] for s in env.entities("selector"))
        assignments.add(tuple(a["props"]["placement_match"] for a in env.entities("applicant")))
    assert len(assignments) > 1
    assert len({tuple(a["props"]["placement_match"] for a in _match(seed, taste=0).entities("applicant"))
                for seed in SEEDS}) == 1  # without personal taste everyone ranks alike: one match


def test_matching_capacity_and_bars_decide_who_is_placed():
    selectors = fg_env.engines.get("matching").source()["inputs"]["selectors"]["default"]
    roomy = [{**s, "capacity": 5, "minimum_quality": 0} for s in selectors]
    assert _match(0, selectors=roomy).result().outputs["match_rate"] == 1.0
    assert _match(0).result().outputs["match_rate"] < 1.0


def _support(seed, **inputs):
    return run("population", seed=seed, inputs=inputs or None).outputs["support_share"]


def test_population_responses_vary_with_uncertainty_and_follow_inclination():
    assert len({_support(seed) for seed in range(10)}) > 1
    confidence = {run("population", seed=seed).outputs["average_confidence"] for seed in range(10)}
    assert len(confidence) > 1  # reported confidence comes from the response, not echoed from the inputs
    people = fg_env.engines.get("population").source()["inputs"]["participants"]["default"]
    certain = [{**p, "confidence": 1} for p in people]
    assert len({_support(seed, participants=certain) for seed in SEEDS}) == 1  # no uncertainty, no noise
    keen = [{**p, "inclination": min(1, p["inclination"] + 0.6)} for p in people]
    assert (statistics.fmean(_support(s, participants=keen) for s in SEEDS)
            > statistics.fmean(_support(s) for s in SEEDS))


@pytest.mark.parametrize("engine_id, output", [
    ("matching", "first_choice_rate"), ("population", "support_share"), ("deliberation", "yes"),
    ("legislature", "yes"), ("strategy", "cooperation_rate"), ("contest", "winner"), ("network", "adoption_rate"),
    ("council", "final_mean"), ("dispute", "juror_vote_split"), ("negotiation", "agreement_round"),
])
def test_coded_baselines_give_seed_varying_outcomes(engine_id, output):
    assert len({run(engine_id, seed=seed).outputs[output] for seed in range(10)}) > 1


@pytest.mark.parametrize("engine_id", sorted(engine.id for engine in fg_env.list_engines()))
def test_every_engine_that_ships_policies_binds_one_to_each_acting_type(engine_id):
    contract = fg_env.engines.get(engine_id).source()
    if not contract.get("policies"):
        pytest.skip("no coded policies")
    unbound = [name for name, spec in contract["types"].items() if spec.get("agent") and not spec.get("policy")]
    assert unbound == []


def test_exchange_seats_are_where_participants_trade_by_default():
    coded, random = (run("exchange", who, seed=1, inputs={"bars": 2}).outputs for who in (None, "random"))
    assert coded["pnl_by_kind"]["seats"] != 0 and coded["pnl_by_kind"] != random["pnl_by_kind"]


def _input_extremes():
    for engine in fg_env.list_engines():
        for name, spec in engine.source()["inputs"].items():
            for bound in ("min", "max"):
                if spec.get("type") in ("int", "number") and spec.get(bound) is not None:
                    yield pytest.param(engine.id, name, spec[bound], id=f"{engine.id}-{name}-{bound}")


@pytest.mark.parametrize("engine_id, name, value", list(_input_extremes()))
def test_every_engine_runs_at_both_ends_of_each_declared_input(engine_id, name, value):
    # the market and exchange at their largest simulate thousands of people for many days or passes: their first
    # days or passes show they run at that size; every other engine runs to its end
    rounds = {"market": 1, "exchange": 12}.get(engine_id)
    result = fg_env.engines.load(engine_id, inputs={name: value}, seed=1).run(rounds=rounds)
    # a contest with no judge bound scores by its stand-in rubric: that run is degraded (host_fallback), not broken
    unjudged = result.degraded == ["host_fallback"] and engine_id == "contest"
    assert result.error is None and (rounds is not None or result.ok or unjudged), result.error
    assert not result.output_issues


#: Smaller settings the market and exchange are swept under, so a sweep takes seconds (other inputs keep defaults).
_SWEEP_BASE = {"market": {"days": 60, "sample_size": 80, "launch_day": 5}, "exchange": {"bars": 6, "participants": 40},
               "dispute": {"evidence_rounds": 1}}  # one exhibit a side: a close case, where every jury rule can bite
#: Rounds a sweep stops at where an extreme would run for minutes; outputs are read where it stops.
_SWEEP_ROUNDS = {("market", "sample_size"): 1, ("exchange", "bars"): 24, ("exchange", "participants"): 4}
#: The market's launch inputs act in its chain_launch arm, which runs the baseline's rules too.
_SWEEP_ARM = {"market": "chain_launch"}
#: Declared inputs no output shows when moved alone from one bound to the other, and why.
_INERT = {
    ("exchange", "circuit_breaker_pct"): "0 turns the breaker off and a 50% move within one bar does not happen in "
                                         "a normal session; the breaker_tight arm (3%) shows the breaker at work",
}


def _numeric_ranges():
    for engine in fg_env.list_engines():
        for name, spec in engine.source()["inputs"].items():
            if spec.get("type") in ("int", "number") and spec.get("min") is not None and spec.get("max") is not None:
                yield pytest.param(engine.id, name, spec["min"], spec["max"], id=f"{engine.id}-{name}")


def _swept_outputs(engine_id, name, value):
    engine = fg_env.engines.get(engine_id)
    path = Path(str(files("fg_env.engines").joinpath(engine.path)))
    inputs = {**_SWEEP_BASE.get(engine_id, {}), name: value}
    seeds = (1,) if engine_id in ("market", "exchange") else (1, 2, 3)  # the big engines' outputs move with anything
    return [fg_env.load(path, inputs=inputs, seed=seed, arm=_SWEEP_ARM.get(engine_id))
            .run(rounds=_SWEEP_ROUNDS.get((engine_id, name))).outputs for seed in seeds]


@pytest.mark.parametrize("engine_id, name, low, high", list(_numeric_ranges()))
def test_every_declared_numeric_input_moves_an_output_across_its_range(engine_id, name, low, high):
    """An input a starter advertises must do something: moved from its min to its max it changes an output, unless
    it is listed in _INERT with the reason no output can show it."""
    changed = _swept_outputs(engine_id, name, low) != _swept_outputs(engine_id, name, high)
    assert changed != ((engine_id, name) in _INERT), _INERT.get((engine_id, name),
                                                                f"{name} changed no output of {engine_id}")


def test_the_market_sample_stands_for_the_city_so_capacity_scales_with_it():
    def one_day(size):
        env = fg_env.engines.load("market", inputs={"sample_size": size})
        return env, env.run(rounds=1).outputs

    small, _ = one_day(150)
    large, outputs = one_day(2000)
    capacity = {env: {c["id"]: c["props"]["capacity"] for c in env.entities("cafe")} for env in (small, large)}
    city = sum(row["weight"] for row in fg_env.engines.get("market").source()["inputs"]["households"]["default"])
    assert capacity[small]["bean_there"] == pytest.approx(7000 * 150 / city)  # kept exact: no rounding to whole cups
    assert capacity[large]["bean_there"] == pytest.approx(7000 * 2000 / city)
    assert outputs["turned_away_total"] == 0  # the same city, sampled finer: nobody is turned away on day one


def test_the_chain_launch_refuses_a_launch_after_the_run_ends():
    path = Path(str(files("fg_env.engines").joinpath(fg_env.engines.get("market").path)))
    with pytest.raises(fg_env.InvariantViolation, match="launch_day no later than days"):
        fg_env.load(path, inputs={"days": 28, "launch_day": 40}, arm="chain_launch")


@pytest.mark.parametrize("engine_id, inputs, output", [
    ("population", {"participants": []}, "support_share"),
    ("matching", {"applicants": []}, "match_rate"),
    ("network", {"participants": [], "ties": [], "seeds": []}, "adoption_rate"),
])
def test_a_rate_over_nobody_is_null_not_zero(engine_id, inputs, output):
    assert run(engine_id, inputs=inputs).outputs[output] is None


def test_population_reports_more_confidence_the_more_certain_people_are():
    people = fg_env.engines.get("population").source()["inputs"]["participants"]["default"]

    def reported(confidence):
        table = [{**p, "confidence": confidence} for p in people]
        return statistics.fmean(run("population", seed=s, inputs={"participants": table}).outputs["average_confidence"]
                                for s in SEEDS)

    assert reported(0) < reported(0.5) < reported(1)


@pytest.mark.parametrize("engine_id, kind, at_least", [
    ("population", "person", 100), ("network", "person", 50), ("matching", "applicant", 30),
    ("contest", "contestant", 5),
    ("strategy", "strategist", 8), ("legislature", "member", 21), ("deliberation", "member", 10),
])
def test_engines_ship_realistic_default_sizes(engine_id, kind, at_least):
    assert len(fg_env.engines.load(engine_id).entities(kind)) >= at_least


def test_forward_looking_players_compete_more_as_the_temptation_grows():
    def cooperation(bonus):
        return statistics.fmean(run("strategy", seed=s, inputs={"compete_bonus": bonus}).outputs["cooperation_rate"]
                                for s in SEEDS)

    assert cooperation(50) < cooperation(8) < cooperation(5)
    lookers = _players(a="forward_looking", b="forward_looking")
    moves = run("strategy", inputs={"participants": lookers, "rounds": 4, "mistakes": 0}).outputs
    assert moves["total_competitions"] == 2  # both cooperate until the last round, when nothing is left to protect


def test_coded_negotiators_strike_different_deals_on_different_seeds():
    assert len({json.dumps(run("negotiation", seed=seed).outputs["surplus"]) for seed in range(8)}) > 2
