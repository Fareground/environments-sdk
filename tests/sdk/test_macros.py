"""Parse-time macros: repeated structure generated from data, expanded before mechanisms."""
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.sdk.errors import ContractError
from fg_env.sdk.experiment import Job, run_jobs
from fg_env.sdk.macros import MAX_MACRO_DEPTH, MAX_MACRO_ITEMS

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"

STREETS = ["flop", "turn", "river"]

BETTING = {
    "name": "Streets",
    "clock": {"rounds": 1},
    "types": {"player": {"agent": True, "props": {"bets_{s}": {"for": STREETS, "as": "s", "make": 0}}}},
    "entities": {"ann": {"type": "player"}},
    "actions": {"bet_{s}": {"for": STREETS, "as": "s", "make": {
        "by": "player", "description": "Bet on the {s}.", "do": ["$actor.bets_{s} += 1"], "terminal": True}}},
    "stages": [{"for": STREETS, "as": "s", "index": "i", "make": {"name": "{s}", "actions": ["bet_{s}"], "max_calls": "{i+2}"}}],
    "outputs": {"bets": "$map(player, $it.bets_flop + $it.bets_turn + $it.bets_river)"},
}


def _issues(excinfo):
    return [(issue.path, issue.message) for issue in excinfo.value.issues]


def test_list_items_and_named_entries_are_generated_with_placeholders_keeping_types():
    data = fg_env.expand(BETTING)
    assert [s["name"] for s in data["stages"]] == STREETS
    assert [s["max_calls"] for s in data["stages"]] == [2, 3, 4]  # a lone placeholder keeps the number
    assert list(data["actions"]) == ["bet_flop", "bet_turn", "bet_river"]
    assert data["actions"]["bet_turn"]["do"] == ["$actor.bets_turn += 1"]
    assert data["types"]["player"]["props"] == {"bets_flop": 0, "bets_turn": 0, "bets_river": 0}
    result = fg_env.run(BETTING, lambda wake: wake.call(f"bet_{wake.stage}"), seed=1)
    assert result.ok, result.summary()
    assert result.outputs["bets"] == [3]


def test_ranges_fields_offsets_nested_loops_and_a_macro_under_a_plain_key():
    data = fg_env.expand({"name": "x", "links": {"for": {"range": [1, 7, 3]}, "as": "n", "make": {"from": "p{n}", "to": "p{n+1}"}},
                          "cells": [{"for": [{"row": "a", "cols": [1, 2]}, {"row": "b", "cols": [3]}], "as": "r",
                                     "make": {"for": "{r.cols}", "as": "c", "make": "{r.row}{c}:{r.cols.0}"}}],
                          "grid": {"c_{x}_{y}": {"for": {"range": 2}, "as": "x", "make": {"for": {"range": 2}, "as": "y",
                                                                                           "make": "{x}{y}"}}}})
    assert data["links"] == [{"from": "p1", "to": "p2"}, {"from": "p4", "to": "p5"}]
    assert data["cells"] == ["a1:1", "a2:1", "b3:3"]
    assert data["grid"] == {"c_0_0": "00", "c_0_1": "01", "c_1_0": "10", "c_1_1": "11"}


def test_templates_literal_braces_and_unrelated_placeholders_are_left_alone():
    data = fg_env.expand({"name": "x", "views": {"v_{k}": {"for": ["a"], "as": "k", "make": {
        "show": "{name} {{k}} {$actor.cash|money} {k} {k}s {json}", "json": "{k}"}}}})
    assert data["views"]["v_a"]["show"] == "{name} {{k}} {$actor.cash|money} a as {json}"
    assert fg_env.expand({"name": "x", "l": [{"for": [[1, 2]], "as": "v", "make": "has {v}"}]})["l"] == ["has [1, 2]"]


@pytest.mark.parametrize("contract, path, message", [
    ({"l": [{"for": 3, "as": "x", "make": 1}]}, "l[0].for", "must be a list, a range or a placeholder"),
    ({"l": [{"for": [1], "make": 1}]}, "l[0]", "needs `as`"),
    ({"l": [{"as": "x", "make": 1}]}, "l[0]", "needs `for`"),
    ({"l": [{"for": [1], "as": "x", "make": 1, "where": "x"}]}, "l[0].where", "is not a macro field"),
    ({"m": {"k_{x}": {"for": [1, 1], "as": "x", "make": 1}}}, "m.k_{x}", "'k_1' is given twice"),
    ({"m": {"k_1": 0, "k_{x}": {"for": [1], "as": "x", "make": 1}}}, "m.k_{x}", "'k_1' is given twice"),
    ({"l": [{"for": [{"p": 1}], "as": "x", "make": "{x.q}"}]}, "l[0].make", "x has no field 'q'"),
    ({"l": [{"for": ["a"], "as": "x", "make": "{x+1}"}]}, "l[0].make", "only a whole number can be offset"),
    ({"l": [{"for": [1], "as": "x", "make": {"for": [2], "as": "x", "make": 1}}]}, "l[0].make.as", "already a variable"),
    ({"l": [{"for": [1], "as": "x", "index": "x", "make": 1}]}, "l[0].index", "both name 'x'"),
    ({"l": [{"for": {"range": [0, 5, 0]}, "as": "x", "make": 1}]}, "l[0].for.range", "step cannot be 0"),
    ({"l": [{"for": {"range": 10 ** 9}, "as": "x", "make": 1}]}, "l[0].for.range", "macros generate at most"),
    ({"v": {"for": [1], "as": "x", "make": {"for": [1], "as": "y", "make": 1}, "a": 1}}, "v.a", "is not a macro field"),
    ({"m": {"{x}": {"for": [{"a": 1}], "as": "x", "make": 1}}}, "m.{x}", "must be text"),
])
def test_malformed_macros_are_contract_errors_at_their_path(contract, path, message):
    with pytest.raises(ContractError) as excinfo:
        fg_env.expand({"name": "bad", **contract})
    assert any(p == path and message in m for p, m in _issues(excinfo)), _issues(excinfo)


def test_every_malformed_macro_is_reported_at_once_and_limits_hold():
    with pytest.raises(ContractError) as excinfo:
        fg_env.expand({"name": "bad", "a": [{"for": 1, "as": "x", "make": 1}], "b": [{"for": [1], "make": 1}]})
    assert [p for p, _ in _issues(excinfo)] == ["a[0].for", "b[0]"]
    too_many = {"name": "big", "l": [{"for": {"range": 200}, "as": "x", "make": {"for": {"range": 200}, "as": "y", "make": 1}}]}
    with pytest.raises(ContractError, match=f"more than {MAX_MACRO_ITEMS:,} values"):
        fg_env.expand(too_many)
    deep: dict = {"for": [1], "as": f"v{MAX_MACRO_DEPTH}", "make": 1}
    for level in range(MAX_MACRO_DEPTH):
        deep = {"for": [1], "as": f"v{level}", "make": deep}
    with pytest.raises(ContractError, match=f"nested more than {MAX_MACRO_DEPTH} deep"):
        fg_env.expand({"name": "deep", "l": [deep]})


def test_check_and_load_refuse_a_contract_whose_macros_cannot_expand_like_an_unreadable_file():
    broken = {**BETTING, "stages": [{"for": "flop", "as": "s", "make": {"name": "{s}"}}]}
    for entry in (fg_env.check, fg_env.load):
        with pytest.raises(ContractError) as excinfo:
            entry(broken)
        assert [p for p, _ in _issues(excinfo)] == ["stages[0].for"]


def test_each_imported_file_expands_its_own_macros_and_the_contract_wins(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "bets.json").write_text(json.dumps({
        "actions": {"bet_{s}": {"for": STREETS, "as": "s", "make": {"by": "player", "do": ["$actor.bets_{s} += 1"], "terminal": True}}}}))
    main_contract = {**BETTING, "imports": ["parts/bets.json"],
                     "actions": {"bet_{s}": {"for": ["flop"], "as": "s", "make": {"by": "player", "do": ["$actor.bets_{s} += 5"],
                                                                                     "terminal": True}}}}
    path = tmp_path / "main.json"
    path.write_text(json.dumps(main_contract))
    actions = fg_env.expand(path)["actions"]
    assert actions["bet_flop"]["do"] == ["$actor.bets_flop += 5"] and actions["bet_river"]["do"] == ["$actor.bets_river += 1"]
    results = run_jobs(path, [Job({}, None, 1), Job({}, None, 2)], participants="random", rounds=1, workers=2)
    assert all(r.status != "failed" for r in results), [r.error for r in results]


def test_arm_patches_may_use_macros():
    contract = {**BETTING, "world": {"pot": 0},
                "arms": {"rich": {"patch": {"world": {"bonus_{s}": {"for": STREETS, "as": "s", "make": 2}}}}}}
    env = fg_env.load(contract, seed=1, arm="rich")
    assert env.props["bonus_river"] == 2


def test_expand_with_mechanisms_and_the_cli(tmp_path, capsys):
    data = fg_env.expand(EXAMPLES / "parliament_bill.json", mechanisms=True)
    names = [stage["name"] for stage in data["stages"]]
    assert names[:4] == ["debate_1", "division_1", "debate_2", "division_2"] and "reading_2_debate" not in names
    assert fg_env.expand(fg_env.parse(BETTING)) == fg_env.expand(BETTING)
    path = tmp_path / "bets.json"
    path.write_text(json.dumps(BETTING))
    assert main(["expand", str(path)]) == 0
    assert list(json.loads(capsys.readouterr().out)["actions"]) == ["bet_flop", "bet_turn", "bet_river"]
    path.write_text(json.dumps({**BETTING, "stages": [{"for": 1, "as": "s", "make": {}}]}))
    assert main(["expand", str(path)]) == 1
    assert "stages[0].for" in capsys.readouterr().err


def test_texas_holdem_streets_are_one_macro_that_expands_to_the_explicit_streets():
    streets = fg_env.expand(EXAMPLES / "texas_holdem.json")["mechanisms"]["table"]["streets"]
    deal = [{"game": "cards", "action": "burn"}, {"game": "cards", "action": "deal", "qty": 3, "zone": "board"}]
    assert streets == {"preflop": [], "flop": deal, "turn": [deal[0], {**deal[1], "qty": 1}],
                       "river": [deal[0], {**deal[1], "qty": 1}]}


def test_parliament_readings_written_once_run_every_reading_in_order():
    contract = fg_env.parse(EXAMPLES / "parliament_bill.json")
    phases = contract.mechanisms["bill"]["phases"]
    assert list(phases)[:3] == ["reading_1", "reading_2", "reading_3"]
    assert phases["reading_2"]["next"][0]["to"] == "committee" and phases["reading_1"]["stages"][0]["actions"] == []
    everyone_aye = {"mp": lambda wake: wake.call("vote", {"choice": "aye"}) if wake.stage.startswith("division")
                    else wake.call("amend", {"text": "Exempt small workshops."}) if wake.stage == "bill_committee" else wake.end()}
    result = fg_env.run(EXAMPLES / "parliament_bill.json", everyone_aye, seed=3)
    assert result.status == "ended", result.error
    assert result.outputs["phases"] == ["reading_1", "reading_2", "committee", "reading_3", "assent"]
    assert result.outputs["outcome"] == "assent" and result.outputs["amendments"] == 5
    defeated = fg_env.run(EXAMPLES / "parliament_bill.json", {"mp": lambda wake: wake.call("vote", {"choice": "no"})
                                                              if wake.stage.startswith("division") else wake.end()}, seed=3)
    assert defeated.outputs["phases"] == ["reading_1", "defeated"]
