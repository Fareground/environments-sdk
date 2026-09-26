"""Staffing reductions retain service obligations and account for the continuing labor."""
import copy

import pytest

import fg_env
from fg_env.authoring.scaffold import new


def room(service=5, horizon=6, rate=None):
    contract = new('observed_queue')
    contract['inputs']['jobs']['default'] = [{'at': 0, 'service': service}]
    contract['inputs']['horizon_minutes']['default'] = horizon
    if rate is not None:
        contract['mechanisms']['q']['servers']['technicians']['overrun_cost'] = rate
    return contract


def cut_staff(wake):
    assert wake.call('staff', {'count': 1 if wake.round == 1 else 0}).ok
    wake.end()


@pytest.mark.parametrize('service,horizon,rate,busy,overrun,cost', [
    (5, 6, None, 5, 4, 2.5),
    (5, 6, 60, 5, 4, 4.5),
    (5, 6, 0, 5, 4, .5),  # unpaid service overrun is an explicit modeling choice
    (5.5, 6, None, 5.5, 4.5, 2.75),
    (5, 3, None, 3, 2, 1.5),  # only time within the observation window accrues
])
def test_agent_cannot_obtain_unaccounted_labor_by_cutting_scheduled_staff(service, horizon, rate, busy, overrun, cost):
    env = fg_env.load(room(service, horizon, rate))
    result = env.run(cut_staff)
    assert result.ok
    assert result.outputs['busy_minutes'] == busy
    assert result.outputs['q_overrun_hours'] == pytest.approx(overrun / 60)
    assert result.outputs['q_paid_hours'] == pytest.approx(busy / 60)
    assert result.outputs['staffing_cost'] == pytest.approx(cost)
    assert result.outputs['q_utilisation'] == pytest.approx(1)
    for interval in env.props['q_intervals']:
        if interval['busy']:
            assert interval['utilisation'] == 1
    assert result.outputs['q_staff_by_interval'] == [1] + [0] * (horizon - 1)


def test_overrun_accounting_survives_snapshot_resume():
    contract = room(service=5.5)
    original = fg_env.load(contract)
    original.run(cut_staff, rounds=2)
    restored = fg_env.Env.restore(contract, copy.deepcopy(original.snapshot()))
    assert restored.run(cut_staff).outputs == fg_env.run(contract, cut_staff).outputs


def test_pool_overruns_keep_their_own_hourly_rate():
    contract = room()
    queue = contract['mechanisms']['q']
    queue['channels'] = {'a': {'scheduled': [{'at': 0, 'service': 5}]},
                         'b': {'scheduled': [{'at': 0, 'service': 3.5}]}}
    queue['servers'] = {'a': {'staff': '$world.staff', 'skills': ['a'], 'cost': 30},
                        'b': {'staff': '$world.staff', 'skills': ['b'], 'cost': 60}}
    result = fg_env.run(contract, cut_staff)
    assert result.outputs['busy_minutes'] == 8.5
    assert result.outputs['q_overrun_hours'] == pytest.approx(6.5 / 60)
    assert result.outputs['staffing_cost'] == pytest.approx(6)
    assert result.outputs['q_utilisation'] == 1


def test_overrun_rate_changes_prices_without_resampling_customer_behavior():
    free = room(rate=0)
    free['mechanisms']['q']['channels']['repair'] = {
        'arrivals': 3, 'service': {'dist': 'exponential', 'mean': 2}}
    paid = copy.deepcopy(free)
    paid['mechanisms']['q']['servers']['technicians']['overrun_cost'] = 60
    left = fg_env.run(free, cut_staff, seed=17).outputs
    right = fg_env.run(paid, cut_staff, seed=17).outputs
    assert left['q_customer_events'] == right['q_customer_events']
    assert left['q_overrun_hours'] == right['q_overrun_hours']
    assert right['staffing_cost'] > left['staffing_cost']
