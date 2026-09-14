"""Every shipped example contract checks clean, runs, and reproduces its golden run.

Regenerate goldens after an intended behaviour change: FG_ENV_UPDATE_GOLDEN=1 pytest tests/sdk/test_examples.py
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

import fg_env

EXAMPLES = sorted((Path(__file__).parents[2] / "examples" / "contracts").glob("*.json"))
GOLDEN = Path(__file__).parent / "golden"
ROUNDS = 4


def _fingerprint(result: fg_env.RunResult) -> dict:
    events = json.dumps(result.events, sort_keys=True, default=str)
    return {
        "status": result.status,
        "rounds": result.rounds,
        "metrics": result.metrics,
        "events": len(result.events),
        "events_sha256": hashlib.sha256(events.encode()).hexdigest(),
        "actions": result.stats["actions"],
        "wakes": result.stats["wakes"],
    }


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example_contract(path: Path) -> None:
    errors = [str(i) for i in fg_env.check(path) if i.severity == "error"]
    assert errors == []
    result = fg_env.load(path, seed=7).run(rounds=ROUNDS)
    assert result.status in ("running", "completed", "ended"), result.error
    again = fg_env.load(path, seed=7).run(rounds=ROUNDS)
    assert _fingerprint(again) == _fingerprint(result)  # deterministic under a seed
    golden = GOLDEN / f"{path.stem}.json"
    if os.environ.get("FG_ENV_UPDATE_GOLDEN") or not golden.exists():
        golden.write_text(json.dumps(_fingerprint(result), indent=2, sort_keys=True, default=str) + "\n")
    assert _fingerprint(result) == json.loads(golden.read_text())
