"""The agent-based modelling classics shipped as contracts reproduce their known results, and `fg-env bench` times them."""
import json
from pathlib import Path

import fg_env
from fg_env.__main__ import main
from fg_env.sdk.bench import PHASES, REFERENCE_MODELS, bench

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"


def _run(name, seed=1, **inputs):
    result = fg_env.load(EXAMPLES / f"{name}.json", seed=seed, inputs=inputs).run()
    assert result.status in ("completed", "ended"), result.error
    return result


def test_schelling_mild_preferences_segregate_the_town():
    series = _run("schelling").series["segregation"]
    assert series[-1] > series[0] + 0.15 and series[-1] > 0.7


def test_boltzmann_equal_chances_make_wealth_unequal():
    result = _run("boltzmann_wealth", steps=60)
    assert result.series["gini"][0] < result.series["gini"][-1] and result.outputs["gini"] > 0.45


def test_game_of_life_thins_out_from_a_random_start():
    alive = _run("game_of_life").series["alive"]
    assert 0.02 < alive[-1] < alive[0] - 0.1


def test_forest_fire_crosses_the_forest_only_above_the_critical_density():
    assert _run("forest_fire", density=0.8).outputs["burnt_share"] > 0.8
    assert _run("forest_fire", density=0.4).outputs["burnt_share"] < 0.2


def test_wolves_multiply_while_sheep_last_and_crash_after_them():
    series = _run("wolf_sheep").series
    sheep, wolves = series["sheep"], series["wolves"]
    peak = wolves.index(max(wolves))
    assert max(wolves) > wolves[0] and sheep[peak] < sheep[0] / 2
    assert wolves[-1] < max(wolves) / 2


def test_sugarscape_selects_low_metabolism_and_settles_below_the_start():
    result = _run("sugarscape_lite")
    assert result.outputs["population"] < 60
    assert result.series["metabolism"][-1] < result.series["metabolism"][0] - 0.3


def test_bench_times_the_reference_models_by_phase():
    results = bench(rounds=2)
    assert [r.name for r in results] == list(REFERENCE_MODELS)
    for result in results:
        assert result.rounds == 2 and result.ms_per_round > 0 and set(result.phases) == set(PHASES)
        assert sum(result.phases.values()) <= result.ms_per_round * 1.05 + 0.5


def test_bench_command_prints_a_table_or_json(tmp_path, capsys):
    assert main(["bench", str(EXAMPLES / "forest_fire.json"), "--rounds", "2", "--input", "size=10"]) == 0
    assert "ms/round" in capsys.readouterr().out
    assert main(["bench", str(EXAMPLES / "boltzmann_wealth.json"), "--rounds", "2", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["rounds"] == 2 and "events" in rows[0]["phases_ms_per_round"]
    assert main(["bench", "--rounds", "0"]) == 1
