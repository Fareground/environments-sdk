"""Valid, seed-aligned evidence in business-result comparisons."""
import json

import pytest

import fg_env
from fg_env.analysis.stats import estimate
from fg_env.runtime.measure import RunResult


def run(seed, value, *, invalid=False, failed=False):
    return RunResult(status='failed' if failed else 'completed', ended_by='rounds', rounds=1,
                     seed=seed, arm=None, inputs={}, outputs={'value': value, 'healthy': seed, 'tag': 'x'},
                     metrics={}, series={'stock': [seed, seed+1]},
                     output_issues=[{'path': 'outputs.value', 'message': 'wrong declared type'}] if invalid else [])


def test_invalid_numeric_output_is_omitted_without_losing_healthy_outputs():
    result = fg_env.analysis.compare(run(1, 2), run(1, 1.5, invalid=True))
    assert 'value' not in result.outputs
    assert result.outputs['healthy']['difference'] == 0
    assert any('value' in note and 'invalid' in note for note in result.notes)
    assert 'invalid' in result.report()


def test_reordered_seeds_keep_exact_pairing_and_interval():
    a = [run(1, 100), run(2, 200), run(3, 300)]
    b = [run(3, 310), run(1, 101), run(2, 205)]
    result = fg_env.analysis.compare(a, b)
    row = result.outputs['value']
    expected = estimate([1, 5, 10])
    assert result.paired and row['n_a'] == row['n_b'] == 3
    assert row['difference'] == pytest.approx(expected.mean)
    assert (row['low'], row['high']) == pytest.approx((expected.low, expected.high))
    assert result.series['stock']['rmse'] == 0


@pytest.mark.parametrize('problem', ['invalid', 'missing', 'failed'])
def test_filtering_removes_both_members_of_affected_pair(problem):
    a = [run(1, 10), run(2, 1000), run(3, 30)]
    bad = run(2, 9999, invalid=problem=='invalid', failed=problem=='failed')
    if problem == 'missing':
        bad.outputs['value'] = None
    result = fg_env.analysis.compare(a, [run(3, 33), bad, run(1, 11)])
    row = result.outputs['value']
    assert result.paired and row['difference'] == 2
    assert row['a'] == 20 and row['b'] == 22
    assert row['n_a'] == row['n_b'] == 2
    assert result.notes
    assert result.outputs['healthy']['n_a'] == (2 if problem=='failed' else 3)


def test_one_surviving_pair_has_no_spurious_confidence_interval():
    result = fg_env.analysis.compare([run(1, 10), run(2, 1000)], [run(2, 9999, invalid=True), run(1, 11)])
    row = result.outputs['value']
    assert row['difference'] == 1 and row['n_a'] == row['n_b'] == 1
    assert 'low' not in row and not row.get('clear', False)


def test_partial_seed_overlap_uses_shared_pairs_and_reports_omissions():
    result = fg_env.analysis.compare([run(1, -1000), run(2, 20), run(3, 30)],
                            [run(4, 1000), run(3, 33), run(2, 22)])
    assert result.paired and result.outputs['value']['difference'] == 2.5
    assert any('2 unmatched' in note for note in result.notes)
    assert result.series['stock']['rmse'] == 0


def test_disjoint_samples_keep_independent_comparison_after_per_output_filtering():
    result = fg_env.analysis.compare([run(1, 1), run(2, 3), run(3, 999, invalid=True)],
                            [run(4, 5), run(5, 7), run(6, 9)])
    row = result.outputs['value']
    assert not result.paired and row['difference'] == 5
    assert row['n_a'] == 2 and row['n_b'] == 3 and 'df' in row


@pytest.mark.parametrize('side', ['a', 'b'])
def test_duplicate_seeds_cannot_silently_inflate_sample_size(side):
    a, b = [run(1, 1), run(2, 2)], [run(1, 2), run(2, 3)]
    if side == 'a':
        a[1].seed = 1
    else:
        b[1].seed = 1
    with pytest.raises(ValueError, match='duplicate seeds'):
        fg_env.analysis.compare(a, b)


def test_category_counts_filter_invalid_pairs_as_well():
    a, b = [run(1, 'a'), run(2, 'b')], [run(2, 'c', invalid=True), run(1, 'a')]
    result = fg_env.analysis.compare(a, b)
    assert result.outputs['value']['counts'] == {'a': {'a': 1}, 'b': {'a': 1}}


def test_output_absent_from_first_run_is_still_considered_for_other_pairs():
    a, b = [run(1, 1), run(2, 2)], [run(1, 10), run(2, 3)]
    del a[0].outputs['value']
    result = fg_env.analysis.compare(a, b)
    assert result.outputs['value']['difference'] == 1
    assert result.outputs['value']['n_a'] == 1


@pytest.mark.parametrize('level', [.8, .9, .99])
def test_report_uses_the_requested_confidence_level(level):
    result = fg_env.analysis.compare([run(1, 1), run(2, 2), run(3, 3)],
                            [run(1, 3), run(2, 4), run(3, 7)], level=level)
    assert f'{level:.0%} CI' in result.report()
    assert result.to_dict()['level'] == level
    expected = estimate([2, 2, 4], level)
    assert result.outputs['value']['low'] == pytest.approx(expected.low)


@pytest.mark.parametrize('level', [0, 1, -1, 2, float('nan')])
def test_invalid_level_is_rejected_even_for_single_run_comparisons(level):
    with pytest.raises(ValueError, match='level'):
        fg_env.analysis.compare(run(1, 1), run(1, 2), level=level)


@pytest.mark.parametrize('label', ['n_a', 'difference', 'counts'])
def test_label_cannot_overwrite_result_metadata(label):
    with pytest.raises(ValueError, match='reserved'):
        fg_env.analysis.compare(run(1, 1), run(1, 2), labels=(label, 'treatment'))


def test_comparison_does_not_mutate_raw_results_or_issue_evidence():
    a, b = [run(1, 1), run(2, 2)], [run(2, 4, invalid=True), run(1, 3)]
    before = json.dumps([r.to_dict() for r in a+b], sort_keys=True)
    fg_env.analysis.compare(a, b)
    assert json.dumps([r.to_dict() for r in a+b], sort_keys=True) == before
