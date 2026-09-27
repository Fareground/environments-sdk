"""Output presentation is typed metadata, independent from recorded mechanics."""
import pytest

import fg_env


def contract(presentation):
    return {'name': 'Jobs', 'types': {}, 'outputs': {'jobs': {'expr': '[{id: a, start: 0, finish: 2}]',
            'type': 'list', 'presentation': presentation}}}


def test_authored_columns_timeline_and_identity_preserve_exact_output():
    presentation = {'kind': 'timeline', 'row_key': 'id', 'columns': {'start': {'label': 'Started', 'unit': 'min'}},
                    'timeline': {'start': 'start', 'end': 'finish', 'label': 'id', 'unit': 'min'}}
    parsed = fg_env.parse(contract(presentation))
    assert parsed.outputs['jobs'].presentation.timeline.start == 'start'
    assert fg_env.run(parsed).outputs['jobs'] == [{'id': 'a', 'start': 0, 'finish': 2}]


@pytest.mark.parametrize('presentation', [{'kind': 'timeline'}, {'kind': 'auto', 'timeline': {
    'start': 'start', 'end': 'finish', 'label': 'id'}}, {'kind': 'invented'}, {'row_key': ['id']}])
def test_invalid_presentation_fails_at_contract_load(presentation):
    with pytest.raises(fg_env.ContractError):
        fg_env.parse(contract(presentation))


def test_native_report_uses_authored_table_labels_and_units_without_json_dump():
    source = contract({'kind': 'table', 'columns': {'start': {'label': 'Started', 'unit': 'min'}}})
    report = fg_env.analysis.report(fg_env.run(source), contract=source)
    assert 'Started (min)' in report.markdown
    assert '| 0 |' in report.markdown
    assert '\"start\": 0' not in report.markdown


def test_hidden_metric_stays_recorded_and_explicit_decision_rule_stays_visible():
    source = {'name': 'Visibility', 'types': {}, 'outputs': {
        'visible': {'expr': '2'},
        'internal': {'expr': '37', 'presentation': {'kind': 'hidden'}}}}
    run = fg_env.run(source)
    assert run.outputs['internal'] == 37
    assert 'internal' not in fg_env.analysis.report(run, contract=source).markdown
    assert 'internal 37' in fg_env.analysis.report(run, contract=source, objective='min:internal').markdown
