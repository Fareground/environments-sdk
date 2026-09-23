"""Playthrough golden files: what every seat reads at every decision of one seeded game per example.

A change to rules or agent-facing wording shows up as a diff here. After an intended change, regenerate:
FG_ENV_UPDATE_GOLDEN=1 pytest tests/test_game_playthroughs.py
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import fg_env
from fg_env.game.playthrough import steps_from_text
from game_contracts import GAMES

GOLDEN = Path(__file__).parent / "golden" / "playthroughs"
EXAMPLES = sorted(GAMES.glob("*.json"))
SEED = 1


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example_game_playthrough_matches_its_golden_file(path):
    text = fg_env.rl.playthrough(path, seed=SEED)
    golden = GOLDEN / f"{path.stem}.txt"
    if os.environ.get("FG_ENV_UPDATE_GOLDEN") or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(text)
    assert text == golden.read_text()


def test_a_playthrough_replays_from_its_own_steps():
    text = fg_env.rl.playthrough(GAMES / "leduc_poker.json", seed=7)
    assert fg_env.rl.playthrough(GAMES / "leduc_poker.json", seed=7, steps=steps_from_text(text)) == text
    assert "Seat 0 (p0) reads:" in text and "Legal calls of seat" in text and "Returns: [" in text


def test_the_cli_checks_a_playthrough_against_a_golden_file(tmp_path):
    path = GAMES / "kuhn_poker.json"
    golden = tmp_path / "kuhn.txt"
    run = [sys.executable, "-m", "fg_env", "playthrough", str(path), "--seed", "2"]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    assert subprocess.run(run + ["--out", str(golden)], env=env).returncode == 0
    assert subprocess.run(run + ["--check", str(golden)], env=env, capture_output=True).returncode == 0
    golden.write_text(golden.read_text().replace("Your card", "Their card", 1))
    changed = subprocess.run(run + ["--check", str(golden)], env=env, capture_output=True, text=True)
    assert changed.returncode == 1 and "Your card" in changed.stdout
