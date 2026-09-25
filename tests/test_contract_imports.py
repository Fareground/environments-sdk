"""Contract imports: fragments in separate files, merged with the importing contract's entries winning."""
import json

import pytest

import fg_env
from fg_env.errors import ContractError
from fg_env.experiments.experiment import Job, run_jobs


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _main(folder, imports, **extra):
    return _write(folder / "main.json", {
        "name": "Imported", "clock": {"rounds": 1}, "imports": imports,
        "entities": {"ann": {"type": "player"}},
        "stages": [{"name": "play", "turns": "sequential"}], **extra})


def _problems(excinfo):
    return " ".join(str(issue) for issue in excinfo.value.issues)


def test_imported_sections_merge_nested_imports_resolve_and_the_contract_wins(tmp_path):
    _write(tmp_path / "lib" / "cards.json", {
        "imports": ["more/rules.json"],
        "types": {"player": {"agent": True, "props": {"cash": 10}}},
        "actions": {"pay": {"by": "player", "do": ["$actor.cash -= 1"], "terminal": True},
                    "wave": {"by": "player", "do": [], "terminal": True}}})
    _write(tmp_path / "lib" / "more" / "rules.json",
           {"world": {"pot": 0}, "defs": {"broke": {"args": ["p"], "expr": "$p.cash <= 0"}}})
    main = _main(tmp_path, ["lib/cards.json"],
                 actions={"wave": {"by": "player", "do": ["$world.pot += 1"], "terminal": True}})
    assert [i for i in fg_env.check(main) if i.severity == "error"] == []
    env = fg_env.load(main, seed=1)
    assert {"pay", "wave"} <= set(env.contract.actions) and "broke" in env.contract.defs
    result = env.run(lambda wake: wake.call("wave"))
    assert result.status == "completed", result.error
    assert env.props["pot"] == 1  # the contract's own `wave` won over the imported one


def test_imported_contracts_run_in_worker_processes_without_the_files(tmp_path):
    _write(tmp_path / "parts" / "base.json", {"types": {"player": {"agent": True, "props": {"cash": 10}}},
                                              "actions": {"pay": {"by": "player", "do": ["$actor.cash -= 1"]}}})
    main = _main(tmp_path, ["parts/base.json"])
    results = run_jobs(main, [Job({}, None, 1), Job({}, None, 2)], participants="random", rounds=1, workers=2)
    assert all(r.status != "failed" for r in results), [r.error for r in results]


def test_cycles_escapes_missing_files_and_bad_shapes_are_contract_errors(tmp_path):
    project = tmp_path / "project"
    _write(project / "a.json", {"imports": ["b.json"]})
    _write(project / "b.json", {"imports": ["a.json"]})
    with pytest.raises(ContractError) as cycle:
        fg_env.load(_main(project, ["a.json"]))
    assert "cycle" in _problems(cycle)
    _write(tmp_path / "outside.json", {"world": {"x": 1}})
    with pytest.raises(ContractError) as escape:
        fg_env.load(_main(project, ["../outside.json"]))
    assert "outside the contract's folder" in _problems(escape)
    with pytest.raises(ContractError) as missing:
        fg_env.load(_main(project, ["nope.json"]))
    assert "file not found" in _problems(missing)
    _write(project / "bad.json", {"actions": ["not", "a", "map"]})
    with pytest.raises(ContractError) as shape:
        fg_env.load(_main(project, ["bad.json"]))
    assert "imports[0]" in _problems(shape) and "actions: must be an object" in _problems(shape)
