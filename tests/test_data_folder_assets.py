"""A contract's assets are read from the same data folder as its data files, through checks, experiments in worker
processes and analyses, whether the contract comes as a file or as a dict with ``data_dir=``."""
import json

import fg_env

NOTED = {
    "name": "Noted shop", "clock": {"rounds": 1},
    "inputs": {"level": {"type": "number", "default": 1, "min": 0, "max": 5}},
    "assets": {"note": {"file": "notes/shop.md", "caption": "Opening hours"}},
    "types": {"clerk": {"agent": True, "props": {}}},
    "entities": {"c": {"type": "clerk"}},
    "actions": {"wait": {"by": "clerk", "do": []}},
    "outputs": {"note_size": {"type": "number", "expr": "$asset(note).size * $inputs.level"}},
}


def _folder(tmp_path):
    folder = tmp_path / "shop"
    (folder / "notes").mkdir(parents=True)
    (folder / "notes" / "shop.md").write_text("Open 9 to 5.\n")
    return folder


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


def test_a_dict_contract_finds_its_assets_in_data_dir_for_checks_and_runs(tmp_path):
    folder = _folder(tmp_path)
    assert _errors(fg_env.check(NOTED, data_dir=folder)) == []
    assert fg_env.run(NOTED, seed=1, data_dir=folder).outputs["note_size"] == 13


def test_experiments_in_worker_processes_and_sweeps_find_the_assets(tmp_path):
    folder = _folder(tmp_path)
    exp = fg_env.experiment(NOTED, runs=2, workers=2, data_dir=folder)
    assert not exp.arms["baseline"].failed and exp.arms["baseline"].outputs["note_size"]["mean"] == 13
    swept = fg_env.analysis.sweep(NOTED, {"level": [1, 2]}, runs=1, data_dir=folder, workers=2)
    assert [cell.summary["note_size"].mean for cell in swept.cells] == [13, 26]


def test_a_contract_file_beside_its_assets_needs_no_data_dir(tmp_path):
    folder = _folder(tmp_path)
    path = folder / "shop.json"
    path.write_text(json.dumps(NOTED))
    parsed = fg_env.parse(path)
    assert fg_env.experiment(parsed, runs=2, workers=2).arms["baseline"].outputs["note_size"]["mean"] == 13
