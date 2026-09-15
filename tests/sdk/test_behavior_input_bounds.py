"""Automatically generated integer inputs must remain inside authored bounds."""
import pytest

import fg_env


@pytest.mark.parametrize('base,lower,upper,expected', [
    (2, 1.2, 2.2, []),
    (3, 1.2, 3.2, [2]),
    (-2, -2.2, -1.2, []),
    (-3, -3.2, -1.2, [-2]),
    (0, -0.8, 0.8, []),
    (0, -1.8, 1.8, [-1, 1]),
])
def test_integer_variations_stay_within_fractional_bounds(base, lower, upper, expected):
    c = {'name': 'Staffing', 'clock': {'rounds': 1}, 'types': {'worker': {}},
         'inputs': {'staff': {'type': 'int', 'default': base, 'min': lower, 'max': upper}},
         'outputs': {'constant': '1'}}
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    report = fg_env.behavior_checks(c, runs=1)
    assert report.ok
    if expected:
        finding = next(f for f in report.findings if f.code == 'input_has_no_effect')
        assert finding.evidence['tried'] == expected
        assert report.tested_inputs == ['staff']
    else:
        assert report.tested_inputs == []
        assert report.untested_inputs == ['staff']


@pytest.mark.parametrize('kind,base,lower,upper,expected', [
    ('int', 2, 1.2, None, [3]),
    ('int', -2, None, -1.2, [-3]),
    ('int', 4, 1, 8, [2, 6]),
    ('number', 2, 1.2, 2.2, [1.2, 2.2]),
])
def test_variations_preserve_one_sided_integer_and_continuous_ranges(kind, base, lower, upper, expected):
    spec = {'type': kind, 'default': base}
    if lower is not None:
        spec['min'] = lower
    if upper is not None:
        spec['max'] = upper
    c = {'name': 'Bounded inputs', 'clock': {'rounds': 1}, 'types': {'worker': {}},
         'inputs': {'value': spec}, 'outputs': {'constant': '1'}}
    report = fg_env.behavior_checks(c, runs=1)
    assert report.ok
    finding = next(f for f in report.findings if f.code == 'input_has_no_effect')
    assert finding.evidence['tried'] == expected
