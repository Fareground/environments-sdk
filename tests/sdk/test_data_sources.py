"""Input data files (CSV, JSON, JSONL) read safely from a data directory, and mechanism brief/clock contributions."""
import json
import os

import pytest

import fg_env
from fg_env.sdk.errors import InputError

TOWN = {
    "name": "Data town", "clock": {"rounds": 1},
    "inputs": {"households": {"type": "table", "source": "households.csv",
                              "columns": {"income": "number", "size": "int", "owner": "bool"}},
               "prices": {"type": "list", "source": "prices.json"}},
    "world": {"first_price": "$inputs.prices[0]"},
    "types": {"home": {"agent": True, "props": {"income": 0, "size": 1, "owner": False,
                                                "zip": {"type": "text", "default": ""}}}},
    "population": [{"type": "home", "from": "$inputs.households",
                    "props": {"income": "$row.income", "size": "$row.size", "owner": "$row.owner", "zip": "$row.zip"}}],
    "stages": [{"name": "s", "turns": "sequential"}],
}

CSV = "zip,income,size,owner\n01234,52000,3,yes\n94110,81500.5,1,false\n"


def _folder(tmp_path, contract=TOWN, csv_text=CSV):
    (tmp_path / "households.csv").write_text(csv_text)
    (tmp_path / "prices.json").write_text("[3.5, 4]")
    path = tmp_path / "town.json"
    path.write_text(json.dumps(contract))
    return path


def test_csv_and_json_sources_load_next_to_the_contract(tmp_path):
    env = fg_env.load(_folder(tmp_path), seed=1)
    homes = sorted(env.entities("home"), key=lambda e: e["props"]["zip"])
    assert [h["props"]["zip"] for h in homes] == ["01234", "94110"]  # undeclared columns keep leading zeros
    assert homes[0]["props"]["income"] == 52000 and homes[1]["props"]["income"] == 81500.5
    assert homes[0]["props"]["size"] == 3 and homes[0]["props"]["owner"] is True and homes[1]["props"]["owner"] is False
    assert env.props["first_price"] == 3.5


def test_supplied_inputs_win_and_jsonl_tables_work(tmp_path):
    path = _folder(tmp_path)
    env = fg_env.load(path, seed=1, inputs={"households": [{"zip": "x", "income": 1, "size": 1, "owner": True}]})
    assert [e["props"]["zip"] for e in env.entities("home")] == ["x"]
    (tmp_path / "homes.jsonl").write_text('{"zip": "a", "income": 5, "size": 2, "owner": true}\n\n')
    contract = json.loads(json.dumps(TOWN))
    contract["inputs"]["households"]["source"] = "homes.jsonl"
    env = fg_env.load(contract, seed=1, data_dir=tmp_path)
    assert [e["props"]["income"] for e in env.entities("home")] == [5]


def test_sources_never_read_outside_the_data_directory(tmp_path):
    outside = tmp_path / "secret.csv"
    outside.write_text("zip,income,size,owner\n1,1,1,true\n")
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "prices.json").write_text("[1]")
    os.symlink(outside, folder / "link.csv")

    def attempt(source):
        contract = json.loads(json.dumps(TOWN))
        contract["inputs"]["households"]["source"] = source
        with pytest.raises((InputError, fg_env.ContractError)) as caught:
            fg_env.load(contract, seed=1, data_dir=folder)
        return str(caught.value)

    assert "leads outside the data directory" in attempt("link.csv")
    assert "must be a file name inside the data directory" in attempt("../secret.csv")
    assert "must be a file name inside the data directory" in attempt(str(outside))
    assert "not a supported data file" in attempt("households.xlsx")
    with pytest.raises(InputError, match="needs a data directory"):
        fg_env.load(TOWN, seed=1)  # a dict contract names no folder


def test_bad_cells_are_reported_with_line_and_column(tmp_path):
    path = _folder(tmp_path, csv_text="zip,income,size,owner\n1,lots,1,yes\n")
    with pytest.raises(InputError, match="line 2 column 'income' must be a number"):
        fg_env.load(path, seed=1)


def test_snapshots_restore_without_the_data_files(tmp_path):
    path = _folder(tmp_path)
    contract = fg_env.parse(path)
    env = fg_env.load(path, seed=3)
    snap = json.loads(json.dumps(env.snapshot()))
    (tmp_path / "households.csv").unlink()
    restored = fg_env.Env.restore(contract, snap)
    assert sorted(e["id"] for e in restored.entities("home")) == sorted(e["id"] for e in env.entities("home"))


def test_experiments_read_sources_in_worker_processes(tmp_path):
    result = fg_env.experiment(_folder(tmp_path), runs=2, workers=2)
    assert all(run.status != "failed" for arm in result.arms.values() for run in arm.runs), result.table()


def test_mechanisms_add_rules_text_and_clock_defaults_without_overriding_the_author():
    from fg_env.sdk.registry import mode

    from family_fixtures import Nothing, scratch_family

    with scratch_family("test_rulebook"):
        mode("test_rulebook", "rules", Nothing, "Adds its rules and a clock default.")(
            lambda name, config, contract: {"brief": {"rules": "Mechanism rules."}, "clock": {"unit": "hour", "rounds": 99}})
        contract = {"name": "Rules", "brief": {"rules": "Author rules."}, "clock": {"rounds": 2},
                    "types": {"p": {"agent": True}}, "entities": {"p": {"type": "p"}},
                    "mechanisms": {"book": {"kind": "test_rulebook", "mode": "rules"}},
                    "stages": [{"name": "s", "turns": "sequential"}]}
        parsed = fg_env.parse(contract)
        assert parsed.brief.rules == "Author rules.\n\nMechanism rules."
        assert parsed.clock.rounds == 2 and parsed.clock.unit == "hour"
        assert fg_env.parse(json.loads(json.dumps(contract))).brief.rules.count("Mechanism rules.") == 1
