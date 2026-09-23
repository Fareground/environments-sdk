"""The owner reports of the three business examples, kept as text: a change to their wording, numbers or drivers shows
up as a diff to read rather than a silent drift.

Regenerate after an intended change: FG_ENV_UPDATE_SNAPSHOTS=1 pytest tests/sdk/test_report_snapshots.py
"""
import os
from pathlib import Path

import pytest

import fg_env

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"
SNAPSHOTS = Path(__file__).parent / "report_snapshots"
#: Each example played briefly on a few seeds — its arms and a shorter horizon — with the decision rule its study uses.
CASES = {
    "auto_parts_store": ({"arms": ["lean", "service"], "inputs": {"weeks": 13}},
                         {"objective": "max:reorder_profit", "require": {"shop_fill_rate": ">= 0.95"}}),
    "phone_reseller": ({"arms": [None, "clearance", "service"], "inputs": {"days": 28}},
                       {"objective": "max:buying_profit", "require": {"sales_fill_rate": ">= 0.95"}}),
    "contact_centre": ({"arms": ["recommended", "current", "outage"]}, {"control": "recommended"}),
}


@pytest.mark.parametrize("name", list(CASES))
def test_an_examples_owner_report_reads_exactly_as_kept(name):
    played, rule = CASES[name]
    contract = EXAMPLES / f"{name}.json"
    text = fg_env.analysis.report(fg_env.experiment(contract, runs=3, seed=13, **played), contract=contract, **rule).markdown
    kept = SNAPSHOTS / f"{name}.md"
    if os.environ.get("FG_ENV_UPDATE_SNAPSHOTS") or not kept.exists():
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(text, encoding="utf-8")
    assert text == kept.read_text(encoding="utf-8")
