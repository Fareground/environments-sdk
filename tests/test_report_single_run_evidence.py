"""Missing comparisons cannot establish randomness or a cause."""
import fg_env
from fg_env.runtime.measure import RunResult


def test_deterministic_calculation_report_does_not_invent_chance_or_require_repeats():
    contract = {"fg_env": "2", "name": "Fixed staffing calculation",
                "clock": {"rounds": 1}, "types": {}, "stages": [],
                "outputs": {"completed": "16", "unserved": "5"}}
    result = fg_env.load(contract).run(rounds=1)
    report = fg_env.analysis.report(result, contract=contract)
    text = report.markdown
    assert "completed 16" in text and "unserved 5" in text
    assert "no between-run comparison is available" in text
    assert "do not identify what caused" in text
    assert "does not establish how reliably" in text
    assert "chance" not in text
    assert "run an experiment for" not in text


def test_unknown_generating_process_is_not_labelled_deterministic_or_random():
    result = RunResult(status="completed", ended_by="rounds", rounds=1,
                       seed=7, arm=None, inputs={}, outputs={"sales": 12},
                       metrics={}, series={})
    report = fg_env.analysis.report(result)
    text = report.markdown.lower()
    assert "no between-run comparison is available" in text
    assert "do not identify what caused" in text
    assert "chance" not in text and "deterministic" not in text
