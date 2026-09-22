"""The bundled engines report what actually happened: no fabricated winners, ties as ties, every player paid,
advertised inputs that move outcomes, and verdicts that agree with the evidence they are measured against."""
import statistics

import pytest

import fg_env

SEEDS = range(6)


def run(engine_id, participants=None, *, seed=0, inputs=None):
    return fg_env.load_engine(engine_id, inputs=inputs, seed=seed).run(participants)


def test_contest_without_a_judge_names_no_winner_and_says_why():
    result = run("contest")
    assert result.outputs["winner"] is None  # every entry was scored at the midpoint: a tie, not "the last contestant"
    assert [d["code"] for d in result.diagnostics] == ["host_fallback"]
    assert "judge" in result.diagnostics[0]["message"]


def test_strategy_pays_every_pair_of_players_and_reports_ties():
    three = [{"id": k, "name": k.upper(), "cooperative": True} for k in "abc"]
    result = run("strategy", inputs={"participants": three, "rounds": 2})
    # round-robin: each player meets two others per round, 2 rounds x 2 opponents x mutual_cooperate (3)
    assert result.outputs["top_score"] == 12
    assert result.outputs["winner"] is None  # a three-way tie

    mixed = [{"id": "a", "name": "A", "cooperative": True}, {"id": "b", "name": "B", "cooperative": False},
             {"id": "c", "name": "C", "cooperative": True}]
    result = run("strategy", inputs={"participants": mixed, "rounds": 1})
    assert result.outputs["winner"] == "B" and result.outputs["top_score"] == 10  # the bonus against each cooperator


def _adoption(inputs):
    return statistics.fmean(run("network", seed=seed, inputs=inputs).outputs["adoption_rate"] for seed in range(12))


def test_network_trust_and_time_drive_adoption():
    ties = fg_env.get_engine("network").source()["inputs"]["ties"]["default"]
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

    env = fg_env.load_engine("council", inputs={"outcome": True})
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
    assert {run(engine_id, seed=seed).outputs["outcome"] for seed in range(2)} == {"passed"}


@pytest.mark.parametrize("engine_id", sorted(engine.id for engine in fg_env.list_engines()))
def test_every_engine_that_ships_policies_binds_one_to_each_acting_type(engine_id):
    contract = fg_env.get_engine(engine_id).source()
    if not contract.get("policies"):
        pytest.skip("no coded policies")
    unbound = [name for name, spec in contract["types"].items() if spec.get("agent") and not spec.get("policy")]
    assert unbound == []
