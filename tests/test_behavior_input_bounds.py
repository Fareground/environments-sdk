"""Automatically generated integer inputs must remain inside authored bounds."""
import pytest
import copy

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
    report = fg_env.analysis.behavior_checks(c, runs=1)
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
    report = fg_env.analysis.behavior_checks(c, runs=1)
    assert report.ok
    finding = next(f for f in report.findings if f.code == 'input_has_no_effect')
    assert finding.evidence['tried'] == expected


def test_boundary_checks_find_nested_configured_input_failures_and_preserve_data(tmp_path, capsys):
    c = {'name': 'Nested bounds', 'types': {}, 'clock': {'rounds': 2}, 'world': {'total': 0},
         'inputs': {'rows': {'type': 'table', 'default': [{'settings': {'scale': 2}}], 'fields': {
             'settings': {'type': 'map', 'fields': {
                 'scale': {'type': 'number', 'default': 2, 'min': 0, 'max': 4}}}}}},
         'events': [{'each': '$inputs.rows', 'do': '$world.total += 10 / $it.settings.scale'}],
         'outputs': {'total': '$world.total'}}
    supplied = {'rows': [{'settings': {'scale': 3}}]}
    original, original_inputs = copy.deepcopy(c), copy.deepcopy(supplied)
    ordinary = fg_env.analysis.behavior_checks(c, inputs=supplied, runs=1)
    assert ordinary.ok and ordinary.untested_inputs == ['rows']
    report = fg_env.analysis.behavior_checks(c, inputs=supplied, runs=2, boundaries=True)
    failures = [f for f in report.findings if f.code == 'input_boundary_failure']
    assert not report.ok and len(failures) == 1
    failure = failures[0]
    assert failure.subject == 'inputs.rows[0].settings.scale'
    assert failure.evidence['input_path'] == ['rows', 0, 'settings', 'scale']
    assert failure.evidence['value'] == 0 and 'division by zero' in failure.evidence['error']
    assert len(failure.evidence['seeds']) == 2
    assert report.tested_inputs == ['rows'] and report.untested_inputs == []
    assert c == original and supplied == original_inputs
    limited = fg_env.analysis.behavior_checks(c, runs=1, boundaries=True, max_boundary_cases=1)
    scope = next(f for f in limited.findings if f.code == 'input_boundary_scope')
    assert scope.severity == 'warning' and scope.evidence['limited']
    assert scope.evidence['cases'] == 1
    import json
    from fg_env.__main__ import main
    path = tmp_path / 'bounds.json'
    path.write_text(json.dumps(c))
    assert main(['playtest', str(path), '--boundaries', '--runs', '1', '--rounds', '2', '--json']) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(f['code'] == 'input_boundary_failure' for f in payload['findings'])


def test_boundary_checks_exercise_nested_empty_lists_and_keep_integer_bounds_valid():
    c = {'name': 'Schedules', 'types': {}, 'clock': {'rounds': 2}, 'world': {'value': 0},
         'inputs': {'settings': {'type': 'map', 'default': {}, 'fields': {
             'schedule': {'type': 'list', 'default': [2, 3], 'items': {'type': 'int', 'min': 1.2, 'max': 3.2}},
             'mode': {'type': 'enum', 'values': ['a', 'b'], 'default': 'a'}}}},
         'events': [{'do': '$world.value = $inputs.settings.schedule[$round - 1]'}],
         'outputs': {'value': '$world.value'}}
    report = fg_env.analysis.behavior_checks(c, runs=1, boundaries=True)
    failures = [f for f in report.findings if f.code == 'input_boundary_failure']
    assert {tuple(f.evidence['value']) for f in failures} == {(), (2,)}
    assert all(f.subject == 'inputs.settings.schedule' for f in failures)
    assert not any('must be' in f.message for f in failures)
    for maximum in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            fg_env.analysis.behavior_checks(c, boundaries=True, max_boundary_cases=maximum)


def test_boundary_checks_find_display_labels_used_as_unique_entity_ids():
    c = {'name': 'Editable rows', 'types': {'item': {}}, 'clock': {'rounds': 1},
         'inputs': {'rows': {'type': 'table', 'fields': {'name': {'type': 'text'}},
                            'default': [{'name': 'A'}, {'name': 'B'}]}},
         'population': [{'type': 'item', 'from': '$inputs.rows', 'id': '{$row.name}', 'name': '{$row.name}'}],
         'outputs': {'count': '$count(item)'}}
    assert fg_env.analysis.behavior_checks(c, runs=1).ok
    report = fg_env.analysis.behavior_checks(c, runs=1, boundaries=True)
    failure = next(f for f in report.findings if f.code == 'input_boundary_failure')
    assert failure.evidence['input_path'] == ['rows']
    assert failure.evidence['value'] == [{'name': 'A'}, {'name': 'B'}, {'name': 'A'}]
    assert 'already exists' in failure.evidence['error']
