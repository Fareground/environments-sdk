"""Offline guard for the authoring benchmark: replaying a recorded transcript scores it the same way.

Run with ``PYTHONPATH=src pytest benchmarks/authoring/tests`` (not part of the default test run)."""
import json
from pathlib import Path

from bench import friction, score, scorecard

FIXTURE = Path(__file__).parent / "fixtures" / "weekly_inventory.json"


def test_replay_rechecks_every_write_and_scores_the_last():
    row = score("weekly_inventory", json.loads(FIXTURE.read_text()))

    assert row["writes"] == 3 and row["clean_at"] == 3 and row["final_clean"]
    assert row["check_errors"][0] == ["(contract): not valid JSON"]
    assert "did you mean 'cash'" in row["check_errors"][1][0]
    assert (row["runs_ok"], row["runs"]) == (3, 3)
    assert (row["checks_passed"], row["checks"]) == (4, 4), row["failed_checks"]


def test_friction_groups_messages_and_scorecard_lists_them():
    row = score("weekly_inventory", json.loads(FIXTURE.read_text()))

    assert [f["count"] for f in friction([row, row])] == [2, 2]
    card = scorecard([row], "fixture")
    assert "| weekly_inventory | yes (3) | 3/3 | 4/4 |" in card
    assert "owner has no property '…'" in card


def test_a_broken_environment_fails_its_fidelity_checks():
    transcript = json.loads(FIXTURE.read_text())
    contract = transcript["writes"][-1]
    contract["events"][1]["do"].remove("$it.lost += $want - $units")  # lost sales are never counted
    row = score("weekly_inventory", {**transcript, "writes": [contract]})

    assert [c["check"] for c in row["failed_checks"]] == ["shop_stock_is_conserved"]

