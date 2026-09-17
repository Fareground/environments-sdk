import json

import pytest

import fg_env


ENGINE_IDS = {
    "market", "council", "dispute", "exchange", "legislature", "judged_contest",
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


def test_available_engine_can_clone_customize_and_run(tmp_path):
    target = fg_env.clone_engine("market", tmp_path / "custom_market.json", name="Custom market")
    contract = json.loads(target.read_text())
    assert contract["name"] == "Custom market"
    env = fg_env.load_engine("market", seed=4)
    result = env.run("random", rounds=1)
    assert result.rounds == 1 and result.status == "running"


@pytest.mark.parametrize("engine_id", sorted(ENGINE_IDS))
def test_every_available_engine_loads_as_a_native_sdk_environment(engine_id):
    env = fg_env.load_engine(engine_id, seed=3)
    assert isinstance(env, fg_env.Env)


def test_available_engine_can_be_materialized_for_database_backed_builders():
    contract = fg_env.get_engine("exchange").materialized_source()
    assert "imports" not in contract
    assert "source" not in contract["inputs"]["history"]
    assert contract["inputs"]["history"]["default"]
    assert not [issue for issue in fg_env.check(contract) if issue.severity == "error"]


@pytest.mark.parametrize("engine_id", [
    "legislature", "judged_contest", "deliberation", "population", "network", "matching", "strategy",
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
    records = fg_env.get_engine("population").source()["inputs"]["participants"]["default"]
    cohort = fg_env.sample_records(records, size=3, seed=17, resample=False, source="customer-provided")
    env = fg_env.load_engine("population", inputs={"participants": cohort.records()}, seed=17)
    result = env.run()
    assert result.outputs["population_size"] == 3
    assert result.outputs["support"] + result.outputs["oppose"] + result.outputs["undecided"] == 3
    assert cohort.provenance.selected == 3 and cohort.provenance.resampled is False
    assert "responses" not in json.dumps(env.preview(cohort.records()[0]["id"]))


def test_matching_engine_keeps_preferences_and_selector_thresholds_private():
    env = fg_env.load_engine("matching", seed=5)
    applicant = json.dumps(env.preview("a1"))
    selector = json.dumps(env.preview("s1"))
    assert "s1" in applicant and "minimum_quality" not in applicant
    assert "private minimum quality is 0.50" in selector
    assert "Your preferred selector" not in selector
    result = env.run()
    assert result.outputs["matched_applicants"] == 3
    assert result.outputs["unused_capacity"] == 0


def test_each_new_engine_supports_nontrivial_scenario_customization(tmp_path):
    cases = {
        "legislature": ({"body_name": "School board", "bill": "Adopt a later school start time."}, "passed"),
        "judged_contest": ({"prompt": "Pitch a neighborhood resilience program."}, "winner"),
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
    single = fg_env.load(target, seed=23).run("cooperative")
    assert single.status == "ended"
    assert single.outputs["deal_signed"] is True
    assert single.outputs["terms"] == {"amount": 50, "scope": 70, "timing": 6}

    batch = fg_env.experiment(target, runs=3, seed=23, participants="cooperative")
    runs = batch.arms["baseline"].runs
    assert len(runs) == 3
    assert all(run.status == "ended" and run.outputs["deal_signed"] for run in runs)


def test_negotiation_engine_accepts_sampled_people_and_keeps_positions_private():
    engine = fg_env.get_engine("negotiation")
    records = engine.source()["inputs"]["participants"]["default"]
    cohort = fg_env.sample_records(records, size=2, seed=31, resample=False, source="customer-provided")
    env = fg_env.load_engine("negotiation", inputs={"participants": cohort.records()}, seed=31)

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
    cohort = fg_env.sample_records(
        households, size=10, seed=19, run=0, resample=False,
        id_field="household_id", weight_field="weight", source="starter_households",
    )
    result = fg_env.experiment(
        target, runs=3, seed=19, rounds=1, participants="random",
        inputs={"households": cohort.records(), "sample_size": 10},
    )
    assert len(result.arms["baseline"].runs) == 3
    assert all(run.status == "running" for run in result.arms["baseline"].runs)
    assert result.arms["baseline"].outputs
    assert cohort.provenance.selected == 10 and cohort.provenance.resampled is False
