"""Summaries show numbers readably, outputs and metrics may declare a format, and stored values stay exact."""
import fg_env
from fg_env.runtime.measure import shown

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
    assert "third: 3.3333" in lines and "cash: $10.005" in lines and "shares: [0.3333, 0.6667]" in lines
    assert result.formats == {"cash": "money"}


def test_small_numbers_keep_three_significant_digits():
    assert shown(0.000123456) == "0.000123" and shown(12345.678912) == "12345.6789" and shown(2.0) == "2.0"


def test_an_unknown_format_is_reported_with_a_suggestion():
    contract = {**SHARES, "outputs": {"cash": {"expr": "1", "format": "mony"}}}
    assert ("outputs.cash.format: unknown format 'mony' → did you mean "
            "'money'?") in [str(i) for i in fg_env.check(contract)]


def test_saved_results_keep_their_formats(tmp_path):
    result = fg_env.run(SHARES, seed=1)
    result.save(tmp_path / "run.json")
    assert fg_env.RunResult.load(tmp_path / "run.json").summary() == result.summary()


def test_no_number_an_agent_reads_is_rounded_into_another_value():
    from fg_env.expr.template import apply_format, format_value
    # A chance short of certain is not shown as certain, a small amount is not shown as nothing, and float noise
    # reads as the number it is.
    assert [format_value(v) for v in (0.996, 0.004, -0.004, 1.0812, 2.9999999999999996, 0.30000000000000004)] == \
        ["0.996", "0.004", "-0.004", "1.0812", "3", "0.3"]
    assert [apply_format(v, "money") for v in (0.004, 1.0812, 1234.5, -0.0)] == \
        ["$0.004", "$1.0812", "$1,234.50", "$0.00"]
    assert [apply_format(v, "pct") for v in (0.125, 1 / 3, 0.9996, 1.0, 0.0)] == \
        ["12.5%", "33.3%", "99.96%", "100%", "0%"]
    assert [apply_format(v, "int") for v in (2.5, -2.5, 1.49)] == ["3", "-3", "1"]
