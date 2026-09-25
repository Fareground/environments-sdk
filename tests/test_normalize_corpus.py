"""Every shipped contract and engine starter is already in the current form, and normalizing is idempotent: a rule
that rewrote a current contract, or rewrote its own output again, would change what the goldens pin."""
import json
from pathlib import Path

import pytest
from test_examples import example_params

from fg_env.contract.normalize import normalize

ROOT = Path(__file__).parents[1]
CORPUS = sorted([*(ROOT / "examples" / "contracts").rglob("*.json"),
                 *(ROOT / "src" / "fg_env" / "engines" / "starters").rglob("*.json")])


@pytest.mark.parametrize("path", example_params(CORPUS))
def test_every_shipped_contract_is_written_in_the_current_form(path):
    assert normalize(json.loads(path.read_text()))[1] == []


@pytest.mark.parametrize("path", example_params(CORPUS))
def test_normalizing_twice_is_normalizing_once(path):
    data = json.loads(path.read_text())
    once, _ = normalize(data)
    twice, notes = normalize(once)
    assert twice == once and notes == []


def test_loading_a_parsed_contract_again_keeps_its_notes_as_they_are():
    import fg_env

    contract = fg_env.load({"name": "Old", "types": {"t": {}}, "metrics": {"m": "1"}}).contract
    notes = list(contract._notes)
    for _ in range(40):  # each load used to double them
        contract = fg_env.load(contract).contract
    assert contract._notes == notes
