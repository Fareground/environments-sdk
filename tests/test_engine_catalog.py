import json
from importlib.resources import files
from pathlib import Path

import pytest

import fg_env
from fg_env.host.stubs import StubEvaluator

ENGINE_IDS = {
    "retail", "council", "dispute", "exchange", "legislature", "contest",
    "deliberation", "negotiation", "population", "network", "matching", "strategy",
    "supply_chain", "auction", "contact_centre", "ride_hailing", "epidemic", "hidden_roles",
}


def test_catalog_contains_exactly_the_behavioral_engines():
    catalogue = fg_env.engines.catalog()
    assert len(catalogue) == len(ENGINE_IDS)
    assert {engine.id for engine in catalogue} == ENGINE_IDS
    assert set(catalogue.to_dict()) == {"schema_version", "source", "engines"}
    assert all("presets" not in engine.to_dict() for engine in catalogue)
    assert all("products" not in engine.to_dict() for engine in catalogue)


def test_catalog_ships_every_engine_as_native():
    assert {engine.id for engine in fg_env.engines.list_engines(available=True)} == ENGINE_IDS
    assert fg_env.engines.list_engines(available=False) == []
    assert all(engine.status == "native" for engine in fg_env.engines.list_engines())


@pytest.mark.parametrize("engine_id", sorted(ENGINE_IDS))
def test_every_starter_checks_without_warnings(engine_id):
    path = Path(str(files("fg_env.engines").joinpath(fg_env.engines.get(engine_id).path)))
    hosts = {"judge": StubEvaluator()} if engine_id == "contest" else None  # a contest is scored by its host judge
    assert [str(issue) for issue in fg_env.check(path, hosts=hosts)] == []


def test_available_engine_can_clone_customize_and_run(tmp_path):
    target = fg_env.engines.clone("retail", tmp_path / "custom_market.json", name="Custom market")
    contract = json.loads(target.read_text())
    assert contract["name"] == "Custom market"
    env = fg_env.engines.load("retail", seed=4)
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
    target = fg_env.engines.clone(engine_id, tmp_path / f"{engine_id}.json", name=f"Custom {engine_id}")
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
    assert (result.outputs["placement_matched"] + result.outputs["unmatched_applicants"]
            == len(env.entities("applicant")))


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
        target = fg_env.engines.clone(engine_id, tmp_path / f"custom-{engine_id}.json",
                                     name=f"Scenario using {engine_id}")
        result = fg_env.load(target, inputs=inputs, seed=29).run()
        assert result.status in {"completed", "ended"}
        assert expected_output in result.outputs


def test_negotiation_engine_clone_customize_and_batch(tmp_path):
    target = fg_env.engines.clone("negotiation", tmp_path / "vendor_negotiation.json",
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
    target = fg_env.engines.clone("retail", tmp_path / "neighborhood_market.json",
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
                                  "exchange_flagship/seed_history.csv", "forecast_council.json", "beer_game.json",
                                  "auction_house.json", "contact_centre.json", "contact_centre/history.csv",
                                  "ride_hailing.json", "town_epidemic.json", "werewolf.json"])
def test_examples_that_mirror_an_engine_starter_stay_identical_to_it(name):
    """These examples are published copies of engine starters; the starter is the source, so a fix there reaches both.
    """
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


@pytest.mark.parametrize("capital", [1000, 100_000])
def test_the_exchange_opens_its_book_whatever_median_capital_traders_are_given(capital):
    """The opening liquidity is sized for the orders it seeds, not from the traders' capital (audit 11 engines
    HIGH-4): an ordinary `median_capital` failed the run in its first round."""
    small = {"median_capital": capital, "participants": 20, "seed_bars": 20, "bars": 2, "substeps": 6}
    result = fg_env.run(Path(fg_env.__file__).parent / "engines" / "starters" / "exchange_flagship.json", seed=1,
                        inputs=small)
    assert result.status == "completed", result.error


@pytest.mark.parametrize("starter, inputs, said", [
    ("exchange_flagship.json", {"history": [{"day": 1, "open": 100, "high": 101, "low": 99, "close": 100,
                                             "volume": 1000}]}, "inputs.history: must have at least 2 rows"),
    ("coffee_market.json", {"cafes": []}, "inputs.cafes: must have at least 1 row"),
    ("ride_hailing.json", {"demand_profile": [{"hour": 6, "requests_per_hour": 10, "downtown_share": 0.3}]},
     "needs a row for every clock hour the shift covers"),
])
def test_an_engine_refuses_a_table_too_short_to_run_with_the_fix(starter, inputs, said):
    """A short or empty table is refused when the run is set up, naming the input, as other engines do, rather than
    failing a run with an error that points at the rules (audit 11 engines M5); a list or table input's `min` and
    `max` bound its rows."""
    path = Path(fg_env.__file__).parent / "engines" / "starters" / starter
    with pytest.raises((fg_env.InputError, fg_env.RunError), match=said):
        fg_env.load(path, inputs=inputs)


def test_the_chain_cafe_is_in_the_market_only_in_its_launch_arm():
    """In the base arm the chain café never opens, so it is not built at all: no share to list (audit 11 engines LOW-3)
    and no agent that never plays (audit 12)."""
    path = Path(fg_env.__file__).parent / "engines" / "starters" / "coffee_market.json"
    for arm, chain in ((None, False), ("chain_launch", True)):
        env = fg_env.load(path, seed=1, arm=arm)
        result = env.run()
        assert result.ok, result.degraded
        assert any(cafe["props"]["chain"] for cafe in env.entities("cafe")) is chain
        assert {name for name, _ in result.outputs["market_shares"]} == {cafe["name"] for cafe in env.entities("cafe")
                                                                         if cafe["props"]["cups_total"]}


def test_a_network_whose_ties_name_people_not_in_its_table_says_so():
    """A shorter participants table than the ties used to crash the build naming an edge (audit 12 engines M1)."""
    for rows in ([], [{"id": "p1", "name": "P1", "group": "A", "receptivity": 0.5}]):
        with pytest.raises(fg_env.InvariantViolation, match="inputs.ties names someone who is not in"):
            fg_env.engines.load("network", inputs={"participants": rows, "seeds": []}).run("random")


def test_a_contact_centre_with_no_calls_runs_on_every_seed():
    """`calls_scale: 0` drew a negative scale from its uncertainty; arrivals are never below zero (engines M2)."""
    runs = [fg_env.engines.load("contact_centre", inputs={"calls_scale": 0}, seed=seed).run() for seed in range(6)]
    assert {result.status for result in runs} == {"completed"}


@pytest.mark.parametrize("name, inputs, why", [
    ("coffee_market.json", {"households": []}, "inputs.households"),
    ("contact_centre.json", {"weekday_profile": []}, "inputs.weekday_profile"),
    ("contact_centre.json", {"hours_profile_se": [1.0]}, "inputs.hours_profile"),
])
def test_an_empty_or_mismatched_input_table_fails_up_front_naming_the_input(name, inputs, why):
    """An invariant that reads only the inputs is a law of the inputs, held before anything is built from them, so a
    bad table fails with its own `why` naming the input (audit 14 mech M4)."""
    starter = Path(__file__).resolve().parents[1] / "src" / "fg_env" / "engines" / "starters" / name
    with pytest.raises(fg_env.RunError, match="does not hold for these inputs") as failed:
        fg_env.load(starter, inputs=inputs, seed=1)
    assert why in str(failed.value)
