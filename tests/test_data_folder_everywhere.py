"""A contract that reads CSV inputs and builds its population from them can be checked, run and analysed through
every entry point: the data folder (the contract file's folder, or ``data_dir=``) travels with the contract."""
import json
import shutil
from pathlib import Path

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.experiments import workers
from fg_env.experiments.experiment import Job, run_jobs
from fg_env.host.stubs import StubFeed

STORE = Path(__file__).parent / "fixtures" / "store" / "store.json"


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


def _elsewhere(tmp_path):
    """The contract as a dict, with its data folder somewhere else."""
    data = tmp_path / "shared"
    shutil.copytree(STORE.parent / "data", data / "data")
    return json.loads(STORE.read_text()), data


def test_check_reads_data_files_beside_the_contract_file():
    assert _errors(fg_env.check(STORE)) == []


def test_check_of_a_dict_contract_reads_data_files_from_data_dir(tmp_path):
    contract, folder = _elsewhere(tmp_path)
    assert _errors(fg_env.check(contract, data_dir=folder)) == []
    missing = _errors(fg_env.check(contract))
    assert missing and "needs a data directory" in missing[0].message


def test_a_parsed_contract_remembers_its_data_folder_through_loads_and_batches():
    parsed = fg_env.parse(STORE)
    assert fg_env.load(parsed, seed=1).run().status == "completed"
    results = run_jobs(parsed, [Job({}, None, 1), Job({"demand_scale": 2.0}, None, 2)], workers=2)
    assert [r.status for r in results] == ["completed", "completed"]


def test_parsing_with_data_dir_leaves_the_original_contract_untouched(tmp_path):
    contract, folder = _elsewhere(tmp_path)
    plain = fg_env.parse(contract)
    moved = fg_env.analysis.runner.as_contract(plain, folder)
    assert plain._folder is None and moved._folder == str(folder)


def test_experiment_workers_read_data_files_in_other_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "chunk_size", lambda jobs, size, seconds, started: 1)  # however short the runs
    contract, folder = _elsewhere(tmp_path)
    result = fg_env.experiment(contract, runs=3, workers=2, data_dir=folder)
    assert not result.arms["baseline"].failed and result.arms["baseline"].outputs["units"]["n"] == 3


def test_every_analysis_reads_the_data_folder(tmp_path):
    contract, folder = _elsewhere(tmp_path)
    common = {"runs": 2, "data_dir": folder, "workers": 2}
    fitted = fg_env.analysis.calibrate(contract, {"units": 250}, {"demand_scale": {"low": 0.5, "high": 2.0}}, budget=6,
                                       **common)
    assert 0.5 <= fitted.params["demand_scale"] <= 2.0
    cases = [{"name": "a", "inputs": {"demand_scale": 1.0}, "outcome": 230},
             {"name": "b", "inputs": {"demand_scale": 1.5}, "outcome": 320}]
    assert len(fg_env.analysis.backtest(contract, cases, "units", **common).cases) == 2
    assert fg_env.analysis.sweep(contract, {"demand_scale": [0.8, 1.2]}, **common).cells
    assert fg_env.analysis.sensitivity(contract, ["demand_scale"], "units", **common).ranking
    assert fg_env.analysis.precision(contract, "units", relative_se=0.5, max_runs=4, batch=2, data_dir=folder).runs >= 2
    assert fg_env.analysis.behavior_checks(contract, runs=2, data_dir=folder).runs == 2
    chained = fg_env.analysis.chain(contract, contract, {"demand_scale": "lost"}, runs=2, data_dir=folder,
                                    second_data_dir=folder)
    assert chained.scenarios["point"]["failed"] == 0


def test_the_command_line_takes_a_data_folder(tmp_path, capsys):
    contract, folder = _elsewhere(tmp_path)
    path = tmp_path / "store.json"
    path.write_text(json.dumps(contract))
    assert main(["check", str(path)]) == 1
    assert main(["check", str(path), "--data-dir", str(folder)]) == 0
    assert main(["sweep", str(path), "--data-dir", str(folder), "--param", "demand_scale=0.8,1.2", "--runs", "2"]) == 0
    assert "demand_scale" in capsys.readouterr().out


PRICED = {"name": "Priced", "clock": {"rounds": 3},
          "inputs": {"level": {"type": "number", "default": 1, "min": 0, "max": 5}},
          "world": {"price": {"type": "number", "default": 0}}, "types": {},
          "feeds": {"price": {"host": "prices", "into": "world.price", "query": "$round"}},
          "outputs": {"value": {"type": "number", "expr": "$world.price * $inputs.level"}}}


def test_analyses_and_checks_pass_hosts_to_every_run():
    feed = StubFeed(lambda request: 10 + request["round"])
    assert _errors(fg_env.check(PRICED, hosts={"prices": feed})) == []
    swept = fg_env.analysis.sweep(PRICED, {"level": [1, 2]}, runs=2, hosts={"prices": feed}, workers=2)
    assert [cell.summary["value"].mean for cell in swept.cells] == [13, 26]
    cases = [{"name": "one", "inputs": {"level": 1}, "outcome": 13},
             {"name": "two", "inputs": {"level": 2}, "outcome": 26}]
    tested = fg_env.analysis.backtest(PRICED, cases, "value", runs=2, hosts={"prices": feed}, workers=2)
    assert tested.scores["mae_of_median"] == 0
    fitted = fg_env.analysis.calibrate(PRICED, {"value": 39}, {"level": {"low": 1, "high": 5}}, runs=1, budget=12,
                              hosts={"prices": feed}, workers=2)
    assert fitted.params["level"] == pytest.approx(3, abs=0.05)
