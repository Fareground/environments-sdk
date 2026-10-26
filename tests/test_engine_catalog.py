"""The engine catalog: the kernel ships no environment, and a platform registers a folder of its own (a manifest and the
starters it lists). These tests read the small catalog conftest.py registers (tests/fixtures/engines)."""
import json
import shutil
from pathlib import Path

import pytest

import fg_env

FIXTURES = Path(__file__).parent / "fixtures" / "engines"


def test_the_sdk_ships_no_engine_of_its_own():
    bundled = fg_env.engines.EngineCatalog.from_directory(Path(fg_env.engines.__file__).parent)
    assert list(bundled) == []
    assert {engine.id for engine in fg_env.engines.list_engines()} == {"negotiation", "contact_centre"}


def test_a_registered_engine_clones_with_its_files_and_loads(tmp_path):
    target = fg_env.engines.clone("contact_centre", tmp_path / "desk.json", name="Help desk")
    assert json.loads(target.read_text())["name"] == "Help desk"
    assert (tmp_path / "contact_centre" / "history.csv").is_file()  # its data file, beside it
    assert fg_env.check(target, rounds=0) is not None
    env = fg_env.engines.load("negotiation", seed=4)
    assert isinstance(env, fg_env.Env) and env.run("random", rounds=1).rounds == 1
    inlined = fg_env.engines.get("contact_centre").materialized_source()
    assert all("source" not in spec for spec in inlined.get("inputs", {}).values() if isinstance(spec, dict))


def test_an_unknown_engine_names_the_closest_one():
    with pytest.raises(fg_env.engines.EngineNotFound, match="did you mean 'negotiation'"):
        fg_env.engines.get("negotiaton")


def test_a_second_catalog_adds_its_engines_and_a_clash_is_refused(tmp_path):
    other = tmp_path / "other"
    (other / "starters").mkdir(parents=True)
    shutil.copy(FIXTURES / "starters" / "multi_issue_negotiation.json", other / "starters" / "deal.json")
    entry = {"id": "deal", "title": "Deal", "summary": "a deal", "status": "native", "path": "starters/deal.json"}
    (other / "manifest.json").write_text(json.dumps({"engines": [entry]}))
    try:
        assert "deal" in {engine.id for engine in fg_env.engines.register(other)}
        assert fg_env.engines.load("deal", seed=1) is not None
        assert fg_env.engines.register(other) is not None  # the same folder again changes nothing
    finally:
        fg_env.engines.unregister(other)
    assert "deal" not in {engine.id for engine in fg_env.engines.list_engines()}
    (other / "manifest.json").write_text(json.dumps({"engines": [{**entry, "id": "negotiation"}]}))
    with pytest.raises(ValueError, match="'negotiation' is in two registered catalogs"):
        fg_env.engines.register(other)
    assert len(fg_env.engines.list_engines()) == 2
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        fg_env.engines.register(tmp_path)


def test_the_cli_and_the_guide_list_the_registered_engines(tmp_path, capsys):
    from fg_env.__main__ import main

    assert main(["engines"]) == 0
    listing = capsys.readouterr().out
    assert "negotiation" in listing and "fg-env new --engine" in listing
    path = tmp_path / "deal.json"
    assert main(["new", "--engine", "negotiation", str(path)]) == 0
    assert main(["check", str(path)]) == 0
    page = fg_env.guide()
    assert "`negotiation`" in page and "`contact_centre`" in page
