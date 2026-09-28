"""Servers finishing what they started after staff drops are on duty, and paid, while they serve."""
import copy

import pytest

import fg_env


def room(service=5, horizon=6):
    return {
        'name': 'Repair desk', 'clock': {'rounds': horizon, 'unit': 'minute'}, 'world': {'staff': 1},
        'types': {'manager': {'agent': True}}, 'entities': {'manager': {'type': 'manager'}},
        'actions': {'staff': {'by': 'manager', 'params': {'count': {'type': 'int', 'min': 0, 'max': 3}},
                              'do': '$world.staff = $params.count'}},
        'stages': [{'name': 'staffing'}],
        'mechanisms': {'q': {'kind': 'economy', 'mode': 'queue', 'unit': 'minute',
                             'channels': {'repair': {'scheduled': [{'at': 0, 'service': service}]}},
                             'servers': {'technicians': {'staff': '$world.staff', 'cost': 30}}}},
        'outputs': {'busy_minutes': '$world.q_totals.busy', 'staffing_cost': '$world.q_totals.cost'}}


def cut_staff(wake):
    assert wake.call('staff', {'count': 1 if wake.round == 1 else 0}).ok
    wake.end()


@pytest.mark.parametrize('service,horizon,busy,overrun,cost', [
    (5, 6, 5, 4, 2.5),
    (5.5, 6, 5.5, 4.5, 2.75),
    (5, 3, 3, 2, 1.5),  # only time within the run accrues
])
def test_cutting_staff_does_not_give_unpaid_labour(service, horizon, busy, overrun, cost):
    env = fg_env.load(room(service, horizon))
    result = env.run(cut_staff)
    assert result.ok
    assert result.outputs['busy_minutes'] == busy
    assert result.outputs['q_overrun_hours'] == pytest.approx(overrun / 60)
    assert result.outputs['q_paid_hours'] == pytest.approx(busy / 60)
    assert result.outputs['staffing_cost'] == pytest.approx(cost)  # every serving minute at the pool's cost
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


def test_each_pool_pays_its_overrun_at_its_own_cost():
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
