import json

import fg_env


def test_catalog_contains_every_public_arena_game_and_behavioral_engine():
    catalogue = fg_env.engines.catalog()
    public_arena = {
        preset.id
        for engine in catalogue.list(product="arena")
        for preset in engine.presets
        if preset.product == "arena" and not preset.hidden
    }
    assert len(public_arena) == 32
    assert {"chess", "poker_tournament", "debate", "trading_crypto"} <= public_arena
    assert {"process_flow", "system_dynamics"} == set(catalogue.excluded)
    assert {"commodity_market", "crypto_market", "forex_market", "prediction_market",
            "securities_trading", "stock_market", "courtroom_trial"} == set(catalogue.retired)
    assert catalogue.replaced_by_native == ("exchange",)
    assert {
        "market", "council", "dispute", "exchange", "legislature", "judged_contest",
        "deliberation", "negotiation", "population", "network", "matching", "strategy",
    } <= {engine.id for engine in catalogue}


def test_native_starter_can_clone_customize_and_run(tmp_path):
    target = fg_env.clone_engine("market", tmp_path / "custom_market.json", name="Custom market")
    contract = json.loads(target.read_text())
    assert contract["name"] == "Custom market"
    env = fg_env.load_engine("market", seed=4)
    result = env.run("random", rounds=1)
    assert result.rounds == 1 and result.status == "running"


def test_native_starter_can_be_materialized_for_database_backed_builders():
    contract = fg_env.get_engine("exchange").preset("sdk").materialized_source()
    assert "imports" not in contract
    assert "source" not in contract["inputs"]["history"]
    assert contract["inputs"]["history"]["default"]
    assert not [issue for issue in fg_env.check(contract) if issue.severity == "error"]


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


def test_legacy_arena_engine_is_runnable_from_sdk():
    world = fg_env.load_engine("tic_tac_toe", seed=3, max_rounds=1)
    world.run()
    assert world.current_round == 1


def test_catalog_marks_compatibility_and_hides_retired_duplicates():
    exchange = fg_env.get_engine("exchange")
    assert exchange.status == "native"
    assert exchange.preset("sdk").native
    assert "stock_market" not in {preset.id for preset in exchange.presets}
    assert "courtroom_trial" not in {
        preset.id for engine in fg_env.list_engines() for preset in engine.presets
    }
    assert fg_env.get_engine("chess").status == "legacy_compatible"
    assert fg_env.get_engine("avalon").preset().hidden


def test_every_bundled_preset_has_a_runnable_sdk_entrypoint():
    loaded = []
    for engine in fg_env.list_engines():
        for preset in engine.presets:
            kwargs = {} if preset.native else {"max_rounds": 1}
            fg_env.load_engine(engine.id, preset=preset.id, seed=0, **kwargs)
            loaded.append((engine.id, preset.id))
    assert len(loaded) == 58  # 54 compatibility presets + four native reference starters
