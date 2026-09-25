"""Every shipped example contract checks clean, runs, and reproduces its golden run.

Regenerate goldens after an intended behaviour change: FG_ENV_UPDATE_GOLDEN=1 pytest tests/test_examples.py
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

import fg_env


def example_params(paths):
    """``paths`` as test parameters named by their stem."""
    return [pytest.param(path, id=path.stem) for path in paths]


EXAMPLES = example_params(sorted((Path(__file__).parents[1] / "examples" / "contracts").glob("*.json")))
GOLDEN = Path(__file__).parent / "golden"
ROUNDS = 4
#: How many leading events a golden shows as text.
FIRST_EVENTS = 10


def _stable(value):
    """Floats to 10 significant digits: Python 3.12 made float sum() more exact, and goldens must hold on every
    supported Python."""
    if isinstance(value, float):
        return float(f"{value:.10g}")
    if isinstance(value, list):
        return [_stable(v) for v in value]
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()}
    return value


def _line(event: dict) -> str:
    """One event as a readable line: round, kind and actor, then its text (or its data when it has none)."""
    who = f" {event['actor']}" if event.get("actor") else ""
    said = event.get("text") or json.dumps(_stable(event.get("data", {})), sort_keys=True, default=str)
    return f"r{event['round']} {event['kind']}{who}: {said}"


def _fingerprint(result: fg_env.RunResult) -> dict:
    """The first events as readable lines, so a changed golden shows where a run first went another way; the hash
    still pins every event after them."""
    events = json.dumps(_stable(result.events), sort_keys=True, default=str)
    return {
        "status": result.status,
        "rounds": result.rounds,
        "metrics": _stable(result.metrics),
        "events": len(result.events),
        "first_events": [_line(event) for event in result.events[:FIRST_EVENTS]],
        "events_sha256": hashlib.sha256(events.encode()).hexdigest(),
        "actions": result.stats["actions"],
        "wakes": result.stats["wakes"],
    }


@pytest.mark.parametrize("path", EXAMPLES)
def test_example_contract(path: Path) -> None:
    errors = [str(i) for i in fg_env.check(path) if i.severity == "error"]
    assert errors == []
    result = fg_env.load(path, seed=7).run(rounds=ROUNDS)
    assert result.status in ("running", "completed", "ended"), result.error
    again = fg_env.load(path, seed=7).run(rounds=ROUNDS)
    assert _fingerprint(again) == _fingerprint(result)  # deterministic under a seed
    golden = GOLDEN / f"{path.stem}.json"
    if os.environ.get("FG_ENV_UPDATE_GOLDEN"):
        golden.write_text(json.dumps(_fingerprint(result), indent=2, sort_keys=True, default=str) + "\n")
    assert golden.exists(), f"no golden for {path.name}: write it with FG_ENV_UPDATE_GOLDEN=1, then commit it"
    assert _fingerprint(result) == json.loads(golden.read_text())


@pytest.mark.parametrize("path", EXAMPLES)
def test_example_resumes_exactly(path: Path) -> None:
    """A run split by a JSON snapshot, or stopped part-way through a round, ends exactly like one straight run."""
    straight = fg_env.load(path, seed=11).run(rounds=ROUNDS).to_dict()

    env = fg_env.load(path, seed=11)
    env.run(rounds=1)
    if not env.finished:
        env = fg_env.Env.restore(path, json.loads(json.dumps(env.snapshot())))
        env.run(rounds=ROUNDS - 1)
    assert env.result().to_dict() == straight

    points = {"n": 0}

    def stop_part_way(_env: fg_env.Env) -> bool:
        points["n"] += 1
        return points["n"] == 7

    env = fg_env.load(path, seed=11)
    env.run(rounds=ROUNDS, stop=stop_part_way)
    if env.status == "stopped":
        env.run(rounds=ROUNDS - env.round + (1 if env.state.in_round else 0))
    assert env.result().to_dict() == straight


#: Seeds every example plays to its end with random agents (more with FG_ENV_SLOW=1).
FULL_SEEDS = (1, 2, 3, 4, 5) if os.environ.get("FG_ENV_SLOW") else (1,)


@pytest.mark.parametrize("seed", FULL_SEEDS)
@pytest.mark.parametrize("path", EXAMPLES)
def test_example_plays_to_its_end_with_random_agents(path: Path, seed: int) -> None:
    """The goldens stop after a few rounds; rules that only start later (a mediator from round 7) must work too."""
    from _leaks import SMALL

    result = fg_env.load(path, seed=seed, inputs=SMALL.get(path.stem)).run("random")
    assert result.status in ("completed", "ended"), result.error
