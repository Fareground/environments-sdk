"""`fg_env.migrate` and `fg-env migrate` rewrite an earlier form into the current one; loading an earlier form works
with exactly one warning that says how to migrate it, and a current contract gets none."""
import json
import warnings

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.contract.layout import dumps

EARLIER = {
    "fg_env": "1",
    "name": "Old lake",
    "clock": {"rounds": 2},
    "world": {"fish": 10},
    "types": {"fisher": {"agent": True, "props": {"caught": 0}}},
    "population": [{"type": "fisher", "count": 2}],
    "events": [{"phase": "end", "do": "$world.fish += 1"}],
    "metrics": {"fish": "$world.fish"},
}


def test_migrate_returns_the_current_form_and_a_note_per_rewrite():
    current, notes = fg_env.migrate(EARLIER)
    assert current["fg_env"] == "2" and "population" not in current and "metrics" not in current
    assert current["entities"] == {"fisher": {"type": "fisher", "count": 2}}
    assert current["events"] == [{"on": "round.end", "do": "$world.fish += 1"}]
    assert current["outputs"] == {"fish": {"expr": "$world.fish", "series": True}}
    assert len(notes) == 4 and all(": " in note for note in notes)
    assert list(current) == ["fg_env", "name", "clock", "world", "types", "entities", "events", "outputs"]
    assert fg_env.migrate(current) == (current, [])


def test_migrate_reads_a_file_or_json_text_but_not_its_imports(tmp_path):
    (tmp_path / "part.json").write_text(json.dumps({"metrics": {"m": "1"}}))
    path = tmp_path / "main.json"
    path.write_text(json.dumps({**EARLIER, "imports": ["part.json"]}))
    current, notes = fg_env.migrate(path)
    assert current["imports"] == ["part.json"] and not any(note.startswith("imports") for note in notes)
    assert fg_env.migrate(json.dumps(EARLIER)) == fg_env.migrate(EARLIER)


def test_loading_an_earlier_form_warns_once_at_the_caller():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        env = fg_env.load(EARLIER, seed=1)
        fg_env.load(env.contract, seed=1)  # a parsed contract warned when it was parsed
    [warning] = caught
    assert warning.category is DeprecationWarning and warning.filename == __file__
    assert "Old lake" in str(warning.message) and "fg_env.migrate" in str(warning.message)


def test_check_gives_one_warning_for_an_earlier_form_and_none_for_the_current_form(tmp_path):
    path = tmp_path / "lake.json"
    path.write_text(json.dumps(EARLIER))
    [issue] = [i for i in fg_env.check(path, rounds=0) if i.path == "fg_env"]
    assert issue.severity == "warning" and "4 rewrite(s)" in issue.message
    assert f"fg-env migrate {path} --write" in issue.fix
    current, _ = fg_env.migrate(path)
    assert not [i for i in fg_env.check(current, rounds=0) if i.path == "fg_env"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        fg_env.load(current, seed=1)


def test_strict_loading_refuses_an_earlier_form():
    with pytest.raises(fg_env.ContractError):
        fg_env.load(EARLIER, strict=True)


def test_the_cli_prints_the_current_form_and_writes_it_only_when_asked(tmp_path, capsys):
    path = tmp_path / "lake.json"
    path.write_text(json.dumps(EARLIER))
    assert main(["migrate", str(path)]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == fg_env.migrate(EARLIER)[0] and "population[0]: now entities.fisher" in err
    assert json.loads(path.read_text()) == EARLIER
    assert main(["migrate", str(path), "--write"]) == 0
    assert "wrote the current form (4 rewrite(s))" in capsys.readouterr().out
    assert path.read_text() == dumps(fg_env.migrate(EARLIER)[0])
    hand_laid = json.dumps(json.loads(path.read_text()), indent=4)
    path.write_text(hand_laid)
    assert main(["migrate", str(path), "--write", "-q"]) == 0
    assert "already current" in capsys.readouterr().out and path.read_text() == hand_laid  # its layout is kept


def test_the_cli_reports_a_contract_it_cannot_migrate(tmp_path, capsys):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({**EARLIER, "calibration": {}}))
    assert main(["migrate", str(path), "--write"]) == 1
    assert "calibration" in capsys.readouterr().err and json.loads(path.read_text())["fg_env"] == "1"


def test_a_contract_is_written_with_sections_in_order_and_short_entries_on_one_line():
    text = dumps({"types": {"t": {"props": {"a": 1}}}, "name": "N", "clock": {"rounds": 3}})
    assert text == '{\n  "name": "N",\n  "clock": {"rounds": 3},\n  "types": {"t": {"props": {"a": 1}}}\n}\n'
    long = {"name": "N", "types": {"t": {}}, "events": [{"do": ["$world.x += 1"] * 12}]}
    assert json.loads(dumps(long)) == long and '"do": [\n' in dumps(long)
