"""A small number of missing decisions must remain visible in exported reports."""
from dataclasses import replace

import pytest

import fg_env


@pytest.mark.parametrize('audience', ['owner', 'analyst'])
@pytest.mark.parametrize('aggregate', [True, False])
def test_isolated_failure_is_disclosed_without_changing_degraded_classification(audience, aggregate):
    contract = {'name': 'Isolated failure', 'types': {}, 'outputs': {'completed': '6'}}
    result = replace(fg_env.run(contract, rounds=1),
                     stats={'failed_turns': 1, 'timeouts': 1} if aggregate else {},
                     agent_stats={'dispatcher': {'wakes': 16, 'failed_turns': 1, 'timeouts': 1}},
                     diagnostics=[{'code': 'some_turns_failed', 'path': 'agents.dispatcher',
                                   'message': 'One turn timed out.', 'fix': 'Inspect the trace.'}])
    assert not result.degraded
    report = fg_env.analysis.report(result, audience, contract=contract)
    assert report.sections[0].title == 'Execution health'
    assert '1 failed turn(s), 1 timeout(s)' in report.markdown
    assert 'degraded execution' not in report.markdown
    assert 'missing decision' in report.markdown
    assert 'Whether these failures changed an outcome requires decision-time evidence' in report.markdown
    assert 'No risk stands out' not in report.markdown
    assert 'Execution health' in str(report.to_dict())


def test_diagnostic_only_failure_does_not_invent_zero_counts():
    result = replace(fg_env.run({'types': {}, 'outputs': {'n': '1'}}, rounds=1), stats={},
                     diagnostics=[{'code': 'some_turns_failed', 'path': 'agents',
                                   'message': 'An older trace records failures.', 'fix': 'Inspect trace.'}])
    text = fg_env.analysis.report(result).markdown
    assert 'failed turn(s) count unavailable' in text
    assert 'timeout(s) count unavailable' in text
    assert '0 failed turn' not in text


def test_incomplete_agent_counts_do_not_hide_known_failure_or_fill_unknown_with_zero():
    result = replace(fg_env.run({'types': {}, 'outputs': {'n': '1'}}, rounds=1), stats={},
                     agent_stats={'a': {'failed_turns': 1}, 'b': {'wakes': 10}}, diagnostics=[])
    text = fg_env.analysis.report(result).markdown
    assert 'Execution health' in text
    assert 'failed turn(s) count unavailable' in text
    assert '| a | Not recorded | 1 | Not recorded |' in text


def test_healthy_run_does_not_get_failed_turn_warning():
    result = fg_env.run({'types': {}, 'outputs': {'n': '1'}}, rounds=1)
    assert 'Execution health' not in fg_env.analysis.report(result).markdown
