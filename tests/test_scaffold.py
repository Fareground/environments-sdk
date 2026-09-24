import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.authoring.scaffold import TEMPLATES, new


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_every_template_checks_clean_and_runs(template):
    contract = new(template)
    assert [str(i) for i in fg_env.check(contract)] == []
    result = fg_env.run(contract, seed=1)
    assert result.ok and result.output_issues == [], result.summary()
    # A type named `agent` reads as `"agent": {"agent": true}`: the flag, not the name, makes a type act.
    assert "agent" not in contract["types"]


def test_new_writes_the_file_named_after_it_and_keeps_an_existing_one(tmp_path):
    path = tmp_path / "harbour_town.json"
    contract = new("shop", path)
    assert json.loads(path.read_text()) == contract and contract["name"] == "Harbour town"
    with pytest.raises(FileExistsError):
        new("duel", path)
    assert new("duel", path, overwrite=True, name="Stones")["name"] == "Stones"


def test_an_unknown_template_suggests_the_closest():
    with pytest.raises(fg_env.ContractError, match="did you mean 'shop'"):
        new("shp")


def test_simulation_with_one_person_retains_wealth_without_needing_a_counterparty():
    env = fg_env.load(new("simulation"), inputs={"people": 1}, seed=1)
    result = env.run()
    assert result.ok and not result.output_issues, result.summary()
    assert result.outputs == {"gini": 0.0, "broke": 0}
    assert result.series["gini"] == [0.0] * 30
    assert env.entities("person")[0]["props"]["wealth"] == 5


def test_host_can_read_detached_record_streams():
    contract = {
        "name": "Journal",
        "clock": {"rounds": 1},
        "types": {"person": {"agent": True}},
        "entities": {"writer": {"type": "person"}},
        "records": {"journal": {"fields": {"text": "text"}}},
        "actions": {"write": {"by": "person", "params": {},
                                  "do": {"post": "journal", "text": "hello"}}},
        "stages": [{"name": "writing", "actions": ["write"], "max_actions": 1}],
    }
    env = fg_env.load(contract, seed=3)
    env.run(lambda wake: wake.call("write"))

    rows = env.records("journal")
    assert rows[0]["text"] == "hello"
    rows[0]["text"] = "changed outside the engine"
    assert env.records("journal")[0]["text"] == "hello"


def test_host_can_read_record_streams_after_a_failed_run():
    contract = {
        "name": "Failed journal",
        "clock": {"rounds": 1},
        "world": {"slots": {"type": "list", "default": [0]}},
        "types": {"person": {"agent": True}},
        "entities": {"writer": {"type": "person"}},
        "records": {"journal": {"fields": {"text": "text"}}},
        "actions": {
            "write": {
                "by": "person",
                "params": {},
                "do": {"post": "journal", "text": "preserved"},
            },
        },
        "events": [{"phase": "end", "do": "$world.slots[2] = 1"}],
        "stages": [{"name": "writing", "actions": ["write"], "max_actions": 1}],
    }
    env = fg_env.load(contract, seed=3)
    result = env.run(lambda wake: wake.call("write"))

    assert result.status == "failed"
    assert env.records("journal")[0]["text"] == "preserved"
    with pytest.raises(fg_env.SnapshotError, match="failed during round 1"):
        env.snapshot()


@pytest.mark.parametrize("people", [0, -1])
def test_simulation_rejects_empty_populations_at_the_input(people):
    with pytest.raises(fg_env.ContractError, match="people"):
        fg_env.load(new("simulation"), inputs={"people": people}, seed=1)


@pytest.mark.parametrize("people", [2, 50])
def test_simulation_counterparty_guard_preserves_existing_exchange_results(people):
    current = new("simulation")
    previous = new("simulation")
    del previous["events"][0]["when"]
    del previous["inputs"]["people"]["min"]
    for seed in (1, 17):
        actual = fg_env.run(current, inputs={"people": people}, seed=seed)
        baseline = fg_env.run(previous, inputs={"people": people}, seed=seed)
        assert actual.ok and not actual.output_issues
        assert actual.series == baseline.series
        assert actual.outputs == baseline.outputs


def test_cli_new_writes_a_contract_that_checks(tmp_path, capsys):
    path = tmp_path / "duel.json"
    assert main(["new", "duel", str(path)]) == 0
    assert "fg-env check" in capsys.readouterr().out
    assert main(["check", str(path)]) == 0
    assert "contract OK" in capsys.readouterr().out
    assert main(["new", "duel", str(path)]) == 1
