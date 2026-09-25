import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.authoring.scaffold import RECIPES, TEMPLATES, new


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
    contract = new("market", path)
    assert json.loads(path.read_text()) == contract and contract["name"] == "Harbour town"
    with pytest.raises(FileExistsError):
        new("board_game", path)
    assert new("board_game", path, overwrite=True, name="Noughts")["name"] == "Noughts"


def test_an_unknown_template_suggests_the_closest():
    with pytest.raises(fg_env.ContractError, match="did you mean 'market'"):
        new("markt")


def test_the_templates_are_the_blank_start_and_every_cookbook_recipe():
    assert list(TEMPLATES) == ["blank", *RECIPES]


def test_the_simulation_conserves_money_and_needs_two_people():
    result = fg_env.run(new("simulation"), seed=1)
    assert result.ok and len(result.series["gini"]) == 30 and result.series["gini"][0] < result.outputs["gini"]
    with pytest.raises(fg_env.ContractError, match="people"):
        fg_env.load(new("simulation"), inputs={"people": 1}, seed=1)


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
        "events": [{"on": "round.end", "do": "$world.slots[2] = 1"}],
        "stages": [{"name": "writing", "actions": ["write"], "max_actions": 1}],
    }
    env = fg_env.load(contract, seed=3)
    result = env.run(lambda wake: wake.call("write"))

    assert result.status == "failed"
    assert env.records("journal")[0]["text"] == "preserved"
    with pytest.raises(fg_env.SnapshotError, match="failed during round 1"):
        env.snapshot()


def test_cli_new_writes_a_contract_that_checks(tmp_path, capsys):
    path = tmp_path / "auction.json"
    assert main(["new", "auction", str(path)]) == 0
    assert "fg-env check" in capsys.readouterr().out
    assert main(["check", str(path)]) == 0
    assert "contract OK" in capsys.readouterr().out
    assert main(["new", "auction", str(path)]) == 1


def test_a_clean_check_says_how_many_rounds_it_played(tmp_path, capsys):
    short = {"name": "Short", "clock": {"rounds": 4}, "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
             "actions": {"go": {"by": "p", "description": "g", "do": []}}, "outputs": {"n": "$round"}}
    path = tmp_path / "short.json"
    path.write_text(json.dumps(short))
    assert main(["check", str(path)]) == 0
    assert "played all 4 round(s) with random agents" in capsys.readouterr().out
