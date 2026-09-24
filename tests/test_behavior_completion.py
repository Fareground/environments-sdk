import pytest

import fg_env


def contract(rounds=3):
    return {'name': 'Settlement', 'types': {'account': {}}, 'clock': {'rounds': rounds},
            'invariants': [{'expr': 'false', 'check': 'end', 'why': 'Unsettled obligations remain.'}]}


def test_capped_runs_report_unchecked_end_obligations():
    report = fg_env.analysis.behavior_checks(contract(), runs=2, rounds=1)
    finding = next(f for f in report.findings if f.code == 'runs_incomplete')
    assert finding.severity == 'warning'
    assert 'end-only invariants' in finding.message.lower()
    assert len(finding.evidence['seeds']) == 2
    assert report.ok  # a requested partial check is not a broken contract
    assert 'nothing suspicious' not in report.report()


def test_full_runs_execute_end_obligations():
    report = fg_env.analysis.behavior_checks(contract(), runs=1)
    assert not report.ok
    assert 'Unsettled obligations remain' in report.report()
    assert not any(f.code == 'runs_incomplete' for f in report.findings)


@pytest.mark.parametrize('cap', [None, 1, 5])
def test_finished_runs_are_not_reported_incomplete(cap):
    c = contract(1)
    c['invariants'] = []
    report = fg_env.analysis.behavior_checks(c, runs=1, rounds=cap)
    assert report.ok
    assert not any(f.code == 'runs_incomplete' for f in report.findings)


def test_natural_early_ending_is_complete():
    c = contract()
    c['invariants'] = []
    c['end'] = [{'when': '$round == 1'}]
    report = fg_env.analysis.behavior_checks(c, runs=1, rounds=1)
    assert not any(f.code == 'runs_incomplete' for f in report.findings)


def test_input_variant_can_be_incomplete_when_baseline_finishes():
    c = contract(5)
    c['invariants'] = []
    c['inputs'] = {'deadline': {'type': 'int', 'default': 1, 'min': 1, 'max': 3}}
    c['end'] = [{'when': '$round >= $inputs.deadline'}]
    report = fg_env.analysis.behavior_checks(c, runs=1, rounds=1)
    finding = next(f for f in report.findings if f.code == 'runs_incomplete')
    assert finding.subject == 'inputs.deadline'
    assert not any(f.code == 'input_has_no_effect' for f in report.findings)


def test_unfinished_variants_do_not_claim_input_has_no_effect():
    c = contract()
    c['inputs'] = {'unused': {'type': 'int', 'default': 2, 'min': 1, 'max': 3}}
    report = fg_env.analysis.behavior_checks(c, runs=2, rounds=1)
    assert not any(f.code == 'input_has_no_effect' for f in report.findings)
    findings = [f for f in report.findings if f.code == 'runs_incomplete']
    assert {f.subject for f in findings} == {'(run)', 'inputs.unused'}
    assert len(next(f for f in findings if f.subject == 'inputs.unused').evidence['seeds']) == 4


def test_failure_and_unfinished_variant_are_both_reported():
    c = contract()
    c['inputs'] = {'value': {'type': 'int', 'default': 2, 'min': 1, 'max': 3}}
    c['invariants'] = [{'expr': '$inputs.value != 1'}]
    report = fg_env.analysis.behavior_checks(c, runs=1, rounds=1)
    assert ('input_breaks_runs', 'inputs.value') in report.codes()
    assert ('runs_incomplete', 'inputs.value') in report.codes()
