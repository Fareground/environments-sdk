"""Summaries show numbers readably, outputs and metrics may declare a format, and stored values stay exact."""
import fg_env
from fg_env.sdk.measure import shown

SHARES = {
    "name": "Shares",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"coins": 10}}},
    "entities": {"a": {"type": "p"}, "b": {"type": "p"}, "c": {"type": "p"}},
    "actions": {"pass": {"by": "p", "do": "$actor.coins += 0"}},
    "metrics": {"third": "$sum(p, $it.coins) / 9"},
    "outputs": {"third": "$metrics.third", "cash": {"expr": "$sum(p, $it.coins) / 3 + 0.005", "format": "money"},
                "shares": "[1 / 3, 2 / 3]"},
}


def test_summary_rounds_floats_and_applies_declared_formats_without_changing_outputs():
    result = fg_env.run(SHARES, seed=1)
    assert result.outputs["third"] == 30 / 9 and result.outputs["shares"] == [1 / 3, 2 / 3]
    lines = result.summary().splitlines()
    assert "third: 3.3333" in lines and "cash: $10.01" in lines and "shares: [0.3333, 0.6667]" in lines
    assert result.formats == {"cash": "money"}


def test_small_numbers_keep_three_significant_digits():
    assert shown(0.000123456) == "0.000123" and shown(12345.678912) == "12345.6789" and shown(2.0) == "2.0"


def test_an_unknown_format_is_reported_with_a_suggestion():
    contract = {**SHARES, "outputs": {"cash": {"expr": "1", "format": "mony"}}}
    assert "outputs.cash.format: unknown format 'mony' → did you mean 'money'?" in [str(i) for i in fg_env.check(contract)]


def test_saved_results_keep_their_formats(tmp_path):
    result = fg_env.run(SHARES, seed=1)
    result.save(tmp_path / "run.json")
    assert fg_env.RunResult.load(tmp_path / "run.json").summary() == result.summary()
