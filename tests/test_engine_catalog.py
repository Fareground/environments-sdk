import json
from importlib.resources import files
from pathlib import Path

import pytest

import fg_env
from fg_env.host.stubs import StubEvaluator


ENGINE_IDS = {
    "market", "council", "dispute", "exchange", "legislature", "contest",
    "deliberation", "negotiation", "population", "network", "matching", "strategy",
}


def test_catalog_contains_only_the_twelve_behavioral_engines():
    catalogue = fg_env.engines.catalog()
    assert len(catalogue) == 12
    assert {engine.id for engine in catalogue} == ENGINE_IDS
    assert set(catalogue.to_dict()) == {"schema_version", "source", "engines"}
    assert all("presets" not in engine.to_dict() for engine in catalogue)
    assert all("products" not in engine.to_dict() for engine in catalogue)


def test_catalog_ships_all_twelve_as_native_engines():
    assert {engine.id for engine in fg_env.list_engines(available=True)} == ENGINE_IDS
    assert fg_env.list_engines(available=False) == []
    assert all(engine.status == "native" for engine in fg_env.list_engines())


#: Warnings the checker gives a starter that are not problems in it. The discussion stages end when everyone is
#: ready, which a coded or model panel reaches but the smoke play's random agents (who keep talking) rarely do in
#: one play; the negotiation's offer table values each offer for its reader through a def called with the reader
#: itself, which the checker cannot tell from reading someone else's private weights.
CHECKER_FALSE_POSITIVES = {
    ("council", "stages.discussion.until"), ("dispute", "stages.deliberation.until"),
    ("deliberation", "stages.forum.until"), ("legislature", "stages.chamber.until"),
    ("negotiation", "views.agreement_table.show"),
}


@pytest.mark.parametrize("engine_id", sorted(ENGINE_IDS))
def test_every_starter_checks_without_warnings(engine_id):
    path = Path(str(files("fg_env.engines").joinpath(fg_env.engines.get(engine_id).path)))
    hosts = {"judge": StubEvaluator()} if engine_id == "contest" else None  # a contest is scored by its host judge
    found = {(engine_id, issue.path): issue.message for issue in fg_env.check(path, hosts=hosts)}
    assert set(found) <= CHECKER_FALSE_POSITIVES, found


def test_available_engine_can_clone_customize_and_run(tmp_path):
    target = fg_env.clone_engine("market", tmp_path / "custom_market.json", name="Custom market")
    contract = json.loads(target.read_text())
    assert contract["name"] == "Custom market"
    env = fg_env.engines.load("market", seed=4)
    result = env.run("random", rounds=1)
    assert result.rounds == 1 and result.status == "running"


@pytest.mark.parametrize("engine_id", sorted(ENGINE_IDS))
def test_every_available_engine_loads_as_a_native_sdk_environment(engine_id):
    env = fg_env.engines.load(engine_id, seed=3)
    assert isinstance(env, fg_env.Env)


def test_available_engine_can_be_materialized_for_database_backed_builders():
    contract = fg_env.engines.get("exchange").materialized_source()
    assert "imports" not in contract
    assert "source" not in contract["inputs"]["history"]
    assert contract["inputs"]["history"]["default"]
    assert not [issue for issue in fg_env.check(contract) if issue.severity == "error"]


@pytest.mark.parametrize("engine_id", [
    "legislature", "contest", "deliberation", "population", "network", "matching", "strategy",
])
def test_new_native_engine_clones_runs_deterministically_and_aggregates(engine_id, tmp_path):
    target = fg_env.clone_engine(engine_id, tmp_path / f"{engine_id}.json", name=f"Custom {engine_id}")
    assert json.loads(target.read_text())["name"] == f"Custom {engine_id}"
    assert not [issue for issue in fg_env.check(target) if issue.severity == "error"]

    first = fg_env.load(target, seed=41).run()
    second = fg_env.load(target, seed=41).run()
    assert first.status in {"completed", "ended"}
    assert first.outputs == second.outputs

    batch = fg_env.experiment(target, runs=3, seed=41)
    runs = batch.arms["baseline"].runs
    assert len(runs) == 3
    assert all(run.status in {"completed", "ended"} and run.outputs for run in runs)


def test_population_engine_uses_a_sampled_persona_cohort_and_keeps_responses_private():
    records = fg_env.engines.get("population").source()["inputs"]["participants"]["default"]
    cohort = fg_env.personas.sample_records(records, size=3, seed=17, resample=False, source="customer-provided")
    env = fg_env.engines.load("population", inputs={"participants": cohort.records()}, seed=17)
    result = env.run()
    assert result.outputs["population_size"] == 3
    assert result.outputs["support"] + result.outputs["oppose"] + result.outputs["undecided"] == 3
    assert cohort.provenance.selected == 3 and cohort.provenance.resampled is False
    assert "responses" not in json.dumps(env.preview(cohort.records()[0]["id"]))


def test_matching_engine_keeps_selector_thresholds_private():
    env = fg_env.engines.load("matching", seed=5)
    applicant = json.dumps(env.preview("a1"))
    selector = json.dumps(env.preview("s1"))
    assert "Selector 1: appeal 0.77" in applicant and "minimum quality" not in applicant
    assert "private minimum quality is 0.52" in selector and "private minimum quality is 0.57" not in selector
    result = env.run()
    assert result.outputs["placement_matched"] + result.outputs["unmatched_applicants"] == len(env.entities("applicant"))


def test_each_new_engine_supports_nontrivial_scenario_customization(tmp_path):
    cases = {
        "legislature": ({"body_name": "School board", "bill": "Adopt a later school start time."}, "passed"),
        "contest": ({"prompt": "Pitch a neighborhood resilience program."}, "winner"),
        "deliberation": ({"question": "Should the cooperative extend opening hours?"}, "decided"),
        "population": ({"question": "Would residents use a weekend shuttle?"}, "support_share"),
        "network": ({"rounds": 3}, "adoption_rate"),
        "matching": ({}, "match_rate"),
        "strategy": ({"rounds": 3, "mutual_cooperate": 4}, "cooperation_rate"),
    }
    for engine_id, (inputs, expected_output) in cases.items():
        target = fg_env.clone_engine(engine_id, tmp_path / f"custom-{engine_id}.json",
                                     name=f"Scenario using {engine_id}")
        result = fg_env.load(target, inputs=inputs, seed=29).run()
        assert result.status in {"completed", "ended"}
        assert expected_output in result.outputs


def test_negotiation_engine_clone_customize_and_batch(tmp_path):
    target = fg_env.clone_engine("negotiation", tmp_path / "vendor_negotiation.json",
                                 name="Vendor renewal negotiation")
    contract = json.loads(target.read_text())
    contract["brief"]["situation"] = "A software buyer and vendor negotiate a renewal."
    contract["mechanisms"]["agreement"]["issues"]["amount"]["description"] = "Annual price in thousands."
    contract["inputs"]["participants"]["default"][0]["role"] = "Software buyer"
    contract["inputs"]["participants"]["default"][1]["role"] = "Software vendor"
    target.write_text(json.dumps(contract))

    assert not [issue for issue in fg_env.check(target) if issue.severity == "error"]
    single = fg_env.load(target, seed=23).run("concession")
    assert single.status == "ended"
    assert single.outputs["deal_signed"] is True
    assert min(single.outputs["surplus"].values()) >= 0

    batch = fg_env.experiment(target, runs=3, seed=23, participants="concession")
    runs = batch.arms["baseline"].runs
    assert len(runs) == 3
    assert all(run.status == "ended" and run.outputs["deal_signed"] for run in runs)


def test_negotiation_engine_accepts_sampled_people_and_keeps_positions_private():
    engine = fg_env.engines.get("negotiation")
    records = engine.source()["inputs"]["participants"]["default"]
    cohort = fg_env.personas.sample_records(records, size=2, seed=31, resample=False, source="customer-provided")
    env = fg_env.engines.load("negotiation", inputs={"participants": cohort.records()}, seed=31)

    party_a = json.dumps(env.preview("party_a"))
    party_b = json.dumps(env.preview("party_b"))
    assert "Walk-away value 11.0" in party_a and "Walk-away value 17.0" not in party_a
    assert "Walk-away value 17.0" in party_b and "Walk-away value 11.0" not in party_b
    assert cohort.provenance.selected == 2
    assert cohort.provenance.resampled is False


def test_cloned_market_uses_sampled_personas_across_an_aggregated_batch(tmp_path):
    target = fg_env.clone_engine("market", tmp_path / "neighborhood_market.json",
                                 name="Neighborhood market")
    contract = json.loads(target.read_text())
    households = contract["inputs"]["households"]["default"]
    cohort = fg_env.personas.sample_records(
        households, size=10, seed=19, run=0, resample=False,
        id_field="household_id", weight_field="weight", source="starter_households",
    )
    result = fg_env.experiment(
        target, runs=3, seed=19, rounds=1, participants="random",
        inputs={"households": cohort.records(), "sample_size": 80},
    )
    assert len(result.arms["baseline"].runs) == 3
    assert all(run.status == "running" for run in result.arms["baseline"].runs)
    assert result.arms["baseline"].outputs
    assert cohort.provenance.selected == 10 and cohort.provenance.resampled is False


@pytest.mark.parametrize("name", ["civil_trial.json", "coffee_market.json", "exchange_flagship.json",
                                  "exchange_flagship/calibration.json", "exchange_flagship/seats.json",
                                  "exchange_flagship/seed_history.csv", "forecast_council.json"])
def test_examples_that_mirror_an_engine_starter_stay_identical_to_it(name):
    """These examples are published copies of engine starters; the starter is the source, so a fix there reaches both."""
    root = Path(__file__).resolve().parents[1]
    starter = root / "src" / "fg_env" / "engines" / "starters" / name
    assert (root / "examples" / "contracts" / name).read_text() == starter.read_text(), (
        f"examples/contracts/{name} drifted from its engine starter: copy the starter over it")


def test_the_cli_lists_the_engines_and_new_clones_one_that_checks(tmp_path, capsys):
    from fg_env.__main__ import main

    assert main(["engines"]) == 0
    listing = capsys.readouterr().out
    assert all(engine_id in listing for engine_id in ENGINE_IDS) and "fg-env new --engine" in listing
    assert fg_env.engines.get("network").summary in listing
    path = tmp_path / "votes.json"
    assert main(["new", "--engine", "legislature", str(path)]) == 0
    assert "fg-env check" in capsys.readouterr().out
    assert json.loads(path.read_text())["name"] == "Votes"
    assert main(["check", str(path)]) == 0
    assert main(["new", "--engine", "legislature", str(path)]) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["new", "--engine", "legislatur", str(tmp_path / "x.json")]) == 1
    assert "did you mean 'legislature'" in capsys.readouterr().err


def test_the_guide_map_lists_every_engine_with_how_to_start_from_it():
    page = fg_env.guide()
    assert "fg-env new --engine" in page and all(f"`{engine_id}`" in page for engine_id in ENGINE_IDS)
