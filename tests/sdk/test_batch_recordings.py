"""Budgets and exposures through every batch that launches runs: experiment, tournament, evaluate, and the CLI."""
import json

import fg_env
from fg_env.__main__ import main

from test_evaluate import PUBLIC_GOODS, SCORE
from test_fork import PRICES, _sell
from test_runtime import SHOP
from test_spectator import TABLE
from test_tournament import RPS
from test_traces import overreacher


def _first_arm(arms):
    return next(iter(arms.values()))


def test_an_experiment_budget_caps_every_run_on_its_own():
    result = fg_env.experiment(SHOP, runs=2, seed=1, budget={"calls": 3})
    runs = _first_arm(result.arms).runs
    assert [run.ended_by for run in runs] == ["budget", "budget"]
    assert all(run.budget["limits"] == {"calls": 3} for run in runs)


def test_with_branch_at_the_shared_rounds_count_toward_every_arms_budget():
    result = fg_env.experiment(PRICES, runs=1, arms=["control", "discount"], branch_at=3, participants={"shop": _sell},
                               budget={"calls": 4})
    for arm in ("control", "discount"):
        run = result.arms[arm].runs[0]
        assert (run.ended_by, run.rounds, run.budget["used"]["calls"]) == ("budget", 4, 4)


def test_experiment_runs_keep_recordings_that_replay():
    result = fg_env.experiment(SHOP, runs=2, seed=3, participants=overreacher, exposures=True)
    run = _first_arm(result.arms).runs[1]
    assert run.exposures["wakes"] and run.events
    assert fg_env.trace(run).replay(SHOP).ok
    assert _first_arm(result.to_dict()["arms"])["runs"][0]["events"]


def test_a_branched_experiments_recordings_replay_from_the_shared_history():
    result = fg_env.experiment(PRICES, runs=1, arms=["control", "discount"], branch_at=3, participants={"shop": _sell},
                               exposures=True)
    discount = result.arms["discount"].runs[0]
    assert discount.exposures["start"]["arm"] == "discount"
    replayed = fg_env.trace(discount).replay(PRICES)
    assert replayed.ok, replayed.message


def test_a_tournament_passes_its_budget_and_recording_to_every_game():
    result = fg_env.tournament(RPS, {"rock": "policy:rock", "paper": "policy:paper"}, budget={"seconds": 60},
                               exposures=True)
    assert all(run.budget["limits"] == {"seconds": 60} for run in result.runs)
    assert all(run.exposures["wakes"] and run.events for run in result.runs)


def test_an_evaluation_keeps_every_run_and_records_exposures_when_asked():
    result = fg_env.evaluate(PUBLIC_GOODS, focal="policy:free_ride", background="policy:cooperate", score=SCORE,
                             runs=2, exposures=True)
    assert len(result.results) == 2 * len(result.pairs)
    assert result.results[0].seed == result.pairs[0]["seed"] == result.results[1].seed
    assert all(run.exposures["wakes"] for run in result.results)


def _file(tmp_path, contract, name):
    path = tmp_path / name
    path.write_text(json.dumps(contract))
    return str(path)


def test_cli_run_prints_exposures_and_saves_spectator_frames(tmp_path, capsys):
    frames = tmp_path / "frames.json"
    assert main(["run", _file(tmp_path, TABLE, "table.json"), "--seed", "1", "--exposures", "--json",
                 "--frames", str(frames)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["exposures"]["wakes"]
    assert [frame["round"] for frame in json.loads(frames.read_text())] == [1, 2, 3]


def test_cli_frames_of_a_contract_without_spectator_views_say_why_they_are_empty(tmp_path, capsys):
    frames = tmp_path / "frames.json"
    assert main(["run", _file(tmp_path, SHOP, "shop.json"), "--seed", "1", "--frames", str(frames)]) == 0
    assert json.loads(frames.read_text()) == []
    assert "no frames" in capsys.readouterr().err


def test_cli_exposures_need_somewhere_to_go(tmp_path, capsys):
    assert main(["experiment", _file(tmp_path, SHOP, "shop.json"), "--runs", "1", "--exposures"]) == 1
    assert "add --json to print it" in capsys.readouterr().err


def test_cli_experiment_and_tournament_take_a_budget_and_exposures(tmp_path, capsys):
    assert main(["experiment", _file(tmp_path, SHOP, "shop.json"), "--runs", "1", "--budget", "calls=3",
                 "--exposures", "--json"]) == 0
    run = _first_arm(json.loads(capsys.readouterr().out)["arms"])["runs"][0]
    assert run["ended_by"] == "budget" and run["exposures"]["wakes"]
    assert main(["tournament", _file(tmp_path, RPS, "rps.json"), "--entrant", "rock=policy:rock",
                 "--entrant", "paper=policy:paper", "--budget", "seconds=60", "--json"]) == 0
    games = json.loads(capsys.readouterr().out)["runs"]
    assert all(game["budget"]["limits"] == {"seconds": 60} for game in games)
