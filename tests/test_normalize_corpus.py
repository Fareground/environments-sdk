"""Every shipped contract and engine starter is already in the current form, and normalizing is idempotent: a rule
that rewrote a current contract, or rewrote its own output again, would change what the goldens pin."""
import json
from pathlib import Path

import pytest

from fg_env.contract.normalize import normalize

ROOT = Path(__file__).parents[1]
CORPUS = sorted([*(ROOT / "examples" / "contracts").rglob("*.json"),
                 *(ROOT / "src" / "fg_env" / "engines" / "starters").rglob("*.json")])


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: str(p.relative_to(ROOT)))
def test_normalizing_twice_is_normalizing_once(path):
    data = json.loads(path.read_text())
    once, _ = normalize(data)
    twice, notes = normalize(once)
    assert twice == once and notes == []
