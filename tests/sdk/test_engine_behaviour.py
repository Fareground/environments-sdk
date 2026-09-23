"""The bundled engines report what actually happened: no fabricated winners, ties as ties, every player paid,
advertised inputs that move outcomes, and verdicts that agree with the evidence they are measured against."""
import statistics

import pytest

import fg_env

SEEDS = range(6)


def run(engine_id, participants=None, *, seed=0, inputs=None):
    return fg_env.engines.load(engine_id, inputs=inputs, seed=seed).run(participants)


def test_contest_without_a_judge_is_decided_by_skill_and_luck_and_says_so():
    result = run("contest")
    assert [d["code"] for d in result.diagnostics] == ["host_fallback"]  # the rubric scores were a stand-in
    assert "judge" in result.diagnostics[0]["message"]
    winners = [run("contest", seed=seed).outputs["winner"] for seed in range(20)]
    assert {"Contestant 1", "Contestant 2"} <= set(winners)  # luck matters...
    assert winners.count("Contestant 1") > winners.count("Contestant 2")  # ...but the more skilled wins more often

    even = [{"id": k, "name": k, "approach": "", "skill": 0.5} for k in ("c1", "c2")]
    assert run("contest", inputs={"participants": even, "luck": 0}).outputs["winner"] is None  # a true tie stays one


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
    assert {run("strategy", seed=seed, inputs={**nice, "mistakes": 0}).outputs["cooperation_rate"] for seed in SEEDS} == {1.0}
    assert statistics.fmean(run("strategy", seed=seed, inputs={**nice, "mistakes": 0.3}).outputs["cooperation_rate"]
                            for seed in SEEDS) < 0.9


def _adoption(inputs):
    return statistics.fmean(run("network", seed=seed, inputs=inputs).outputs["adoption_rate"] for seed in range(12))


def test_network_trust_and_time_drive_adoption():
    ties = fg_env.engines.get("network").source()["inputs"]["ties"]["default"]
    weak = [{**tie, "value": 0.05} for tie in ties]
    assert _adoption({"ties": weak}) < _adoption({"ties": ties}) - 0.1
    assert _adoption({"rounds": 3}) < _adoption({"rounds": 20}) - 0.1


def test_dispute_verdict_follows_the_evidence_it_is_measured_against():
    verdicts = set()
    for seed in SEEDS:
        outputs = run("dispute", seed=seed).outputs
        verdicts.add(outputs["verdict"])
        if outputs["verdict"] != "hung":
            assert (outputs["verdict"] == "liable") == (outputs["net_admitted_strength"] > 0), outputs
    assert {"liable", "not_liable"} <= verdicts  # the starter is not locked to one verdict


def test_council_scores_forecasts_against_the_outcome_and_measures_consensus_by_spread():
    unresolved = run("council").outputs
    assert unresolved["final_brier"] is None

    env = fg_env.engines.load("council", inputs={"outcome": True})
    resolved = env.run().outputs
    finals = [panelist.properties["final"] / 100 for panelist in env.world.entities_of("panelist")]
    assert resolved["final_brier"] == pytest.approx(statistics.fmean((1 - final) ** 2 for final in finals))
    assert resolved["consensus_reached"] is True  # the default averagers converge

    anchored = run("council", "policy:anchored").outputs
    assert anchored["final_range"] > 10 and anchored["consensus_reached"] is False  # all ready, far apart


def test_negotiation_never_binds_a_party_below_its_walk_away():
    deals = 0
    for seed in range(20):
        outputs = run("negotiation", "random", seed=seed).outputs
        if outputs["deal_signed"]:
            deals += 1
            assert min(outputs["surplus"].values()) >= 0, outputs
    assert deals  # random parties still find acceptable deals


@pytest.mark.parametrize("engine_id", ["legislature", "deliberation"])
def test_a_body_that_never_votes_records_the_status_quo(engine_id):
    assert run(engine_id, "idle").outputs["outcome"] == "status_quo"


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
    people = fg_env.engines.get("population").source()["inputs"]["participants"]["default"]
    certain = [{**p, "confidence": 1} for p in people]
    assert len({_support(seed, participants=certain) for seed in SEEDS}) == 1  # no uncertainty, no noise
    keen = [{**p, "inclination": min(1, p["inclination"] + 0.6)} for p in people]
    assert statistics.fmean(_support(s, participants=keen) for s in SEEDS) > statistics.fmean(_support(s) for s in SEEDS)


@pytest.mark.parametrize("engine_id, output", [
    ("matching", "first_choice_rate"), ("population", "support_share"), ("deliberation", "yes"),
    ("legislature", "yes"), ("strategy", "cooperation_rate"), ("contest", "winner"), ("network", "adoption_rate"),
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
