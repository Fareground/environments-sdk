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


def trace(contract):
    contract = copy.deepcopy(contract)
    contract['mechanisms']['q']['record_customers'] = True
    return contract


def test_customer_history_distinguishes_started_completed_and_future_work():
    result = measured(trace(repair()))
    events = result.pop('q_customer_events')
    assert result == measured(repair())  # recording changes observations, never mechanics
    assert [e['time'] for e in events if e['event'] == 'started'] == [0, 5, 10]
    assert [e['time'] for e in events if e['event'] == 'completed'] == [5, 10, 15]
    assert [e['customer'] for e in events if e['event'] == 'completed'] == [1, 2, 3]
    partial = measured(trace(repair(horizon=3)))['q_customer_events']
    assert [e['time'] for e in partial if e['event'] == 'arrived'] == [0, 2]
    assert not [e for e in partial if e['event'] == 'completed']
    started = next(e for e in partial if e['event'] == 'started')
    assert started['scheduled_finish'] == 5  # planned finish beyond horizon is not completion


def test_completion_at_closing_is_observed_once_across_snapshot_continuation():
    contract = trace(repair([{'at': 0, 'service': 3}, {'at': 2, 'service': 2}], horizon=6))
    env = fg_env.load(contract)
    partial = env.run(rounds=3)
    assert [e['time'] for e in partial.outputs['q_customer_events'] if e['event'] == 'completed'] == [3]
    restored = fg_env.Env.restore(contract, copy.deepcopy(env.snapshot()))
    resumed = restored.run().outputs
    assert resumed == measured(contract)
    assert [e['time'] for e in resumed['q_customer_events'] if e['event'] == 'completed'] == [3, 5]


def test_customer_history_records_abandonment_without_inventing_service():
    result = measured(trace(repair([{'at': 0, 'service': 5},
                                    {'at': 1, 'service': 2, 'patience': .5}], horizon=3)))
    events = result['q_customer_events']
    assert [(e['customer'], e['time']) for e in events if e['event'] == 'abandoned'] == [(2, 1.5)]
    assert [e['customer'] for e in events if e['event'] == 'started'] == [1]
    assert not [e for e in events if e['event'] == 'completed']


@pytest.mark.parametrize('inputs, expected', [
    ({'horizon_minutes': 3}, {'completed_jobs': 0, 'unfinished_arrived_jobs': 2, 'busy_minutes': 3}),
    ({'jobs': [{'at': 0, 'service': 2.5}, {'at': 2, 'service': 2.5}, {'at': 4, 'service': 2.5}]},
     {'completed_jobs': 3, 'starts': [0, 2.5, 5], 'finishes': [2.5, 5, 7.5], 'busy_minutes': 7.5}),
    ({'jobs': [{'at': 25, 'service': 5}]}, {'completed_jobs': 0, 'unfinished_arrived_jobs': 0, 'busy_minutes': 0}),
    ({'jobs': []}, {'completed_jobs': 0, 'unfinished_arrived_jobs': 0, 'busy_minutes': 0}),
])
def test_observed_queue_cookbook_exercises_the_audited_boundary_cases(inputs, expected):
    from fg_env.authoring.scaffold import new
    result = fg_env.run(new('observed_queue'), 'policy:one', inputs=inputs, seed=1)
    assert result.ok and not result.output_issues
    assert {key: result.outputs[key] for key in expected} == expected


def test_observed_queue_cookbook_rejects_invalid_rows_before_running():
    from fg_env.authoring.scaffold import new
    for jobs in ([{'at': -1, 'service': 1}], [{'at': 0, 'service': -1}], [{'at': 0}]):
        with pytest.raises(fg_env.ContractError):
            fg_env.load(new('observed_queue'), inputs={'jobs': jobs})


def test_scheduled_callback_history_links_service_to_the_original_customer():
    contract = trace(repair([{'at': 0, 'service': 5}, {'at': 1, 'service': 2}], horizon=8))
    contract['mechanisms']['q']['channels']['repair']['callback'] = {'when': 0, 'accept': 1, 'service_estimate': 3}
    output = measured(contract)
    events = output['q_customer_events']
    assert [(e['customer'], e['time']) for e in events if e['event'] == 'callback'] == [(2, 1)]
    assert [(e['customer'], e['time']) for e in events if e['event'] == 'started'] == [(1, 0), (2, 5)]
    assert [(e['customer'], e['time']) for e in events if e['event'] == 'completed'] == [(1, 5), (2, 7)]
    assert output['q_callbacks'] == 1 and output['q_callbacks_unserved'] == 0


def test_retry_history_preserves_customer_identity_and_distinguishes_attempts():
    contract = trace(repair([{'at': 0, 'service': 5}, {'at': 1, 'service': 1, 'patience': .5}], horizon=7))
    contract['mechanisms']['q']['channels']['repair']['retry'] = {
        'chance': 1, 'delay': {'dist': 'fixed', 'mean': 1}, 'max': 2}
    output = measured(contract)
    events = output['q_customer_events']
    arrivals = [(e['customer'], e['time'], e['retry']) for e in events if e['event'] == 'arrived']
    assert arrivals == [(1, 0, 0), (2, 1, 0), (2, 2.5, 1), (2, 4, 2)]
    assert [(e['customer'], e['time']) for e in events if e['event'] == 'abandoned'] == [(2, 1.5), (2, 3), (2, 4.5)]
    assert [(e['customer'], e['time']) for e in events if e['event'] == 'completed'] == [(1, 5)]
    assert output['q_offered'] == 4  # customer attempts, not four unique people


def test_scheduled_callbacks_require_a_known_service_estimate():
    contract = repair()
    contract['mechanisms']['q']['channels']['repair']['callback'] = {'when': 1, 'accept': 1}
    with pytest.raises(fg_env.ContractError, match='explicit service_estimate'):
        fg_env.load(contract)


def test_future_realized_service_cannot_change_a_current_callback_offer():
    contract = trace(repair([{'at': 0, 'service': 5}, {'at': .1, 'service': 1},
                            {'at': .9, 'service': 1}], horizon=1))
    contract['mechanisms']['q']['channels']['repair']['callback'] = {
        'when': 2, 'accept': 1, 'service_estimate': 1}
    before = measured(contract)['q_customer_events']
    contract['inputs']['jobs']['default'][2]['service'] = 10000
    after = measured(contract)['q_customer_events']
    assert [e for e in before if e['time'] < .9] == [e for e in after if e['time'] < .9]
    assert not [e for e in before if e['event'] == 'callback']


def test_callback_service_estimate_does_not_change_poisson_service_requirements():
    contract = repair(horizon=20)
    channel = {'arrivals': 3, 'service': {'dist': 'fixed', 'mean': 2},
               'callback': {'when': 0, 'accept': 0}}
    contract['mechanisms']['q']['channels']['repair'] = channel
    baseline = measured(contract, seed=1)
    channel['callback']['service_estimate'] = 10000
    assert measured(contract, seed=1) == baseline
    assert baseline['q_aht'] == 2


def test_observed_queue_report_states_the_replay_assumption_and_primary_outcomes():
    from fg_env.authoring.scaffold import new
    contract = new('observed_queue')
    result = fg_env.run(contract, 'policy:one', seed=1)
    report = fg_env.analysis.report(result, contract=contract).markdown
    assert 'supplied arrival timestamps and service durations' in report
    assert 'does not establish future demand uncertainty' in report
    assert 'arrive at random' not in report
    assert 'Completed repairs' in report and 'Unfinished arrived jobs' in report
    assert 'Staffing cost' in report
    assert 'do not establish predictive accuracy' in report
    assert 'pass validation=' not in report
    expected_line = next(line for line in report.splitlines() if 'Expected:' in line)
    assert expected_line.lower().count('staffing cost') == 1
    assert 'service level' not in expected_line


def test_manager_observations_do_not_reveal_future_job_rows():
    from fg_env.authoring.scaffold import new

    def observed(jobs):
        readings = []
        env = fg_env.load(new('observed_queue'), inputs={'jobs': jobs}, seed=1)

        def manager(wake):
            readings.append((wake.brief, wake.update,
                             [(tool.name, tool.description, tool.input_schema) for tool in wake.tools]))
            assert wake.call('staff', {'count': 1}).ok
            wake.end()

        env.run(manager, rounds=6)
        return readings

    first = observed([{'at': 0, 'service': 5}, {'at': 2, 'service': 5}, {'at': 4, 'service': 5}])
    changed = observed([{'at': 0, 'service': 5}, {'at': 2, 'service': 5}, {'at': 10, 'service': 777}])
    # Decisions at t=0 through t=4 see the same past, despite different hidden future work.
    assert first[:5] == changed[:5]
    assert first[5][1] != changed[5][1]  # once the job arrives, the observed queue changes
    assert all({tool[0] for tool in tools} == {'staff', 'end_turn'} for _, _, tools in first)
    assert all('777' not in brief + update for brief, update, _ in changed)
