"""A fingerprint of everything the engine produces on every shipped example, to prove a speed change changed nothing.

Each example runs twice under a fixed seed: with its default participants, and with a reader that takes in the brief,
the update and the provider tool schemas every wake, looks at a view when it can, then acts at random. The events,
metrics, outputs and stats of both runs, and every text and schema the reader saw, are hashed per example.

    PYTHONPATH=src python benchmarks/speed/fingerprint.py > before.txt
    ... change the engine ...
    PYTHONPATH=src python benchmarks/speed/fingerprint.py | diff before.txt -
"""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, List

import fg_env
from fg_env.participants import RandomAgent

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted([*(ROOT / "examples" / "contracts").glob("*.json"),
                   *(ROOT / "examples" / "contracts" / "games").glob("*.json"),
                   *(ROOT / "src" / "fg_env" / "engines" / "starters").glob("*.json")])
#: Rounds per run: enough to reach every stage and event of the examples, few enough to finish in minutes.
ROUNDS = 24


class Reader:
    """Reads what a model would read on each wake, then acts like :class:`RandomAgent`."""

    concurrent = False

    def __init__(self, seen: List[Any]):
        self.seen = seen
        self.act = RandomAgent(seed=5)

    def __call__(self, wake: fg_env.Wake) -> None:
        self.seen.append([wake.entity_id, wake.round, wake.stage, wake.brief, wake.update, wake.tools_for("anthropic")])
        look = next((tool for tool in wake.tools if tool.name == "look"), None)
        if look is not None:
            self.seen.append(wake.call("look", {"view": look.input_schema["properties"]["view"]["enum"][0]}).text)
        self.act(wake)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fingerprint(path: Path) -> str:
    default = fg_env.load(path, seed=3).run(rounds=ROUNDS).to_dict()
    seen: List[Any] = []
    read = fg_env.load(path, seed=4).run(Reader(seen), rounds=ROUNDS).to_dict()
    return f"{_digest(default)} {_digest(read)} {_digest(seen)}"


def _line(path: Path) -> str:
    try:
        line = fingerprint(path)
    except Exception as exc:  # a failure is part of the fingerprint
        line = f"error {type(exc).__name__}: {exc}"
    return f"{path.relative_to(ROOT)} {line}"


def main() -> None:
    with ProcessPoolExecutor() as pool:
        for line in pool.map(_line, EXAMPLES):
            print(line, flush=True)


if __name__ == "__main__":
    sys.exit(main())
