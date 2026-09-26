"""Observed arrivals exercise continuous service without resampling the supplied jobs."""
import copy

import pytest

import fg_env


def repair(jobs=None, horizon=20, staff=1):
    return {
        'name': 'Observed repair queue', 'clock': {'rounds': horizon, 'unit': 'minute'}, 'types': {},
        'inputs': {'jobs': {'type': 'list', 'default': jobs if jobs is not None else
                           [{'at': 0, 'service': 5}, {'at': 2, 'service': 5}, {'at': 4, 'service': 5}]}},
        'mechanisms': {'q': {'kind': 'economy', 'mode': 'queue', 'unit': 'minute',
                             'channels': {'repair': {'scheduled': '$inputs.jobs'}},
                             'servers': {'technicians': {'staff': staff}}}},
        'outputs': {'busy': '$world.q_totals.busy', 'waiting': '$world.q_totals.waiting',
                    'in_service': '$len($world.q_state.busy)'}}


def measured(contract, **kwargs):
    result = fg_env.run(contract, **kwargs)
    assert result.status == 'completed', result.error
    return result.outputs


def test_recorded_jobs_preserve_fractional_fcfs_waits_and_accrued_busy_time():
    baseline = measured(repair())
    assert baseline['q_offered'] == 3 and baseline['q_asa'] == 3
    assert baseline['busy'] == 15 and baseline['q_utilisation'] == .75
    fractional = measured(repair([{'at': t, 'service': 2.5} for t in (0, 2, 4)]))
    assert fractional['q_asa'] == .5  # waits 0, .5, 1; no rounding to minute ticks
    assert fractional['busy'] == 7.5 and fractional['q_utilisation'] == .375
    interrupted = measured(repair(horizon=3))
    assert interrupted['q_offered'] == 2  # arrival at 4 is not yet offered or waiting
    assert interrupted['busy'] == 3 and interrupted['q_utilisation'] == 1
    assert interrupted['waiting'] == 1 and interrupted['in_service'] == 1


def test_boundary_arrivals_are_counted_once_and_future_jobs_are_not_backlog():
    contract = repair([{'at': 0, 'service': 1}, {'at': 1, 'service': 1}, {'at': 3, 'service': 1}], horizon=3)
    result = measured(contract)
    assert result['q_offered_by_interval'] == [1, 1, 0]
    assert result['q_offered'] == 2 and result['waiting'] == 0
    future = measured(repair([{'at': 25, 'service': 5}]))
    assert future['q_offered'] == future['waiting'] == future['busy'] == 0


def test_zero_duration_and_equal_time_rows_keep_order_without_losing_customers():
    result = measured(repair([{'at': 0, 'service': 0}, {'at': 0, 'service': 2},
                              {'at': 0, 'service': 1}], horizon=4))
    assert result['q_offered'] == 3 and result['q_asa'] == pytest.approx(2 / 3)
    assert result['busy'] == 3 and result['waiting'] == 0


@pytest.mark.parametrize('row', [
    {'at': -1, 'service': 1}, {'at': 100, 'service': -1}, {'at': 0, 'service': True},
    {'at': 0, 'service': float('inf')}, {'at': 0, 'service': 1, 'patience': -1},
    {'at': 0, 'service': 1, 'typo': 2}, {'at': 0},
])
def test_invalid_rows_fail_including_unreached_future_rows(row):
    with pytest.raises(fg_env.RunError, match='scheduled'):
        fg_env.run(repair([row]))


def test_scheduled_and_stochastic_sources_cannot_be_silently_combined():
    contract = repair()
    contract['mechanisms']['q']['channels']['repair']['arrivals'] = 10
    with pytest.raises(fg_env.ContractError, match='scheduled rows cannot'):
        fg_env.load(contract)


def test_snapshot_continuation_and_staffing_changes_keep_recorded_arrivals():
    contract = repair(staff='$world.staff')
    contract['world'] = {'staff': 1}
    contract['events'] = [{'on': 'round.start', 'do': '$world.staff = 2 if $round >= 3 else 1'}]
    whole = measured(contract, seed=1)
    assert whole['q_offered'] == 3 and whole['q_asa'] == pytest.approx(1 / 3)
    assert measured(contract, seed=999) == whole
    env = fg_env.load(contract, seed=1)
    env.run(rounds=3)
    restored = fg_env.Env.restore(contract, copy.deepcopy(env.snapshot()))
    result = restored.run()
    assert result.outputs == whole


def test_an_agent_can_change_staffing_without_changing_observed_customers():
    contract = repair(staff='$world.staff')
    contract['world'] = {'staff': 1}
    contract['types'] = {'manager': {'agent': True}}
    contract['entities'] = {'manager': {'type': 'manager'}}
    contract['stages'] = [{'name': 'staffing'}]
    contract['actions'] = {'staff': {'by': 'manager', 'params': {'count': {'type': 'int', 'min': 0, 'max': 3}},
                                     'do': '$world.staff = $params.count'}}
    actions = []

    def controller(wake):
        count = 2 if wake.round >= 3 else 1
        receipt = wake.call('staff', {'count': count})
        assert receipt.ok
        actions.append((wake.round, count))
        wake.end()

    env = fg_env.load(contract, seed=4)
    result = env.run(controller)
    assert result.outputs['q_offered'] == 3 and result.outputs['q_asa'] == pytest.approx(1 / 3)
    assert actions[:3] == [(1, 1), (2, 1), (3, 2)]
