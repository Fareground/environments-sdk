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


def test_catalog_distinguishes_native_engines_from_remaining_phase_one_work():
    assert {engine.id for engine in fg_env.list_engines(available=True)} == {
        "market", "council", "dispute", "exchange", "negotiation",
    }
    assert {engine.id for engine in fg_env.list_engines(available=False)} == {
        "legislature", "judged_contest", "deliberation", "population",
        "network", "matching", "strategy",
    }


def test_available_engine_can_clone_customize_and_run(tmp_path):
    target = fg_env.clone_engine("market", tmp_path / "custom_market.json", name="Custom market")
    contract = json.loads(target.read_text())
    assert contract["name"] == "Custom market"
    env = fg_env.load_engine("market", seed=4)
    result = env.run("random", rounds=1)
    assert result.rounds == 1 and result.status == "running"


@pytest.mark.parametrize("engine_id", ["market", "council", "dispute", "exchange", "negotiation"])
def test_every_available_engine_loads_as_a_native_sdk_environment(engine_id):
    env = fg_env.load_engine(engine_id, seed=3)
    assert isinstance(env, fg_env.Env)


def test_available_engine_can_be_materialized_for_database_backed_builders():
    contract = fg_env.get_engine("exchange").materialized_source()
    assert "imports" not in contract
    assert "source" not in contract["inputs"]["history"]
    assert contract["inputs"]["history"]["default"]
    assert not [issue for issue in fg_env.check(contract) if issue.severity == "error"]


def test_planned_engine_fails_with_an_actionable_message(tmp_path):
    engine = fg_env.get_engine("legislature")
    assert engine.available is False and engine.status == "planned"
    with pytest.raises(fg_env.engines.EngineUnavailable, match="planned for Phase 1 completion"):
        fg_env.clone_engine("legislature", tmp_path / "legislature.json")


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
