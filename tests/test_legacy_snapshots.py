"""Snapshots of the previous format (version 4, taken before a part-way snapshot held its round's cursor) still restore
and continue exactly: one taken between rounds, and one taken part-way through a round, which is replayed to its point
(copying/legacy_snapshot.py). The fixture was written by the engine of that format: a hopscotch race (an atomic stage)
stopped after round 1 and at its 4th safe point, with the result a straight run of 4 rounds reaches."""
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.copying.snapshot import SNAPSHOT_VERSION

ROOT = Path(__file__).parents[1]
SAVED = json.loads((ROOT / "tests" / "fixtures" / "snapshots_v4_hopscotch_race.json").read_text())
CONTRACT = ROOT / SAVED["contract"]


def _continued(snapshot):
    env = fg_env.Env.restore(CONTRACT, snapshot)
    result = env.run("random", rounds=SAVED["rounds"] - env.round + (1 if env.state.in_round else 0)).to_dict()
    return env, {key: result[key] for key in SAVED["result"]}


@pytest.mark.parametrize("taken", ["between", "part_way"])
def test_a_version_4_snapshot_continues_exactly(taken):
    snapshot = SAVED[taken]
    assert snapshot["fg_env_snapshot"] == 4 and ("part_way" in snapshot) == (taken == "part_way")
    _, result = _continued(snapshot)
    assert result == SAVED["result"]


def test_a_version_4_run_stopped_part_way_restores_at_its_point_and_saves_as_the_current_version():
    env = fg_env.Env.restore(CONTRACT, SAVED["part_way"])
    assert env.status == "stopped" and env.state.in_round and env.round == SAVED["part_way_round"]
    again = env.snapshot()
    assert again["fg_env_snapshot"] == SNAPSHOT_VERSION and "cursor" in again and "part_way" not in again
    assert _continued(json.loads(json.dumps(again)))[1] == SAVED["result"]
