"""Business calendar models follow real month/year boundaries."""
import datetime as dt

import pytest

import fg_env
from fg_env.patterns.timebase import Calendar, days_covered, moment, to_t
from fg_env.stdlib.dates import calendar_date


def clock(start, unit, step=1):
    return Calendar(start, unit, step, 'rounds', 1)


@pytest.mark.parametrize('start,unit,dates', [
    ('2025-01-31', 'month', ['2025-01-31', '2025-02-28', '2025-03-31']),
    ('2024-01-31', 'month', ['2024-01-31', '2024-02-29', '2024-03-31']),
    ('2024-02-29', 'year', ['2024-02-29', '2025-02-28', '2026-02-28']),
])
def test_model_moments_preserve_start_and_clamp_only_short_months(start, unit, dates):
    c = clock(start, unit)
    assert [moment(c, t).date().isoformat() for t in range(3)] == dates


def test_month_clock_labels_keep_the_authored_start_day():
    assert [calendar_date('2025-01-31', 'month', 1, i) for i in range(3)] == [
        '2025-01-31', '2025-02-28', '2025-03-31']


@pytest.mark.parametrize('start,unit,lengths', [
    ('2025-01-01', 'month', [31, 28, 31]),
    ('2024-01-01', 'month', [31, 29, 31]),
    ('2025-01-31', 'month', [28, 31, 30]),
    ('2024-01-01', 'year', [366, 365, 365]),
])
def test_calendar_rounds_cover_exact_intervals_without_gaps_or_overlaps(start, unit, lengths):
    c = clock(start, unit)
    periods = [days_covered(c, t) for t in range(3)]
    assert [len(days) for days in periods] == lengths
    for first, second in zip(periods, periods[1:]):
        assert first[-1] + dt.timedelta(days=1) == second[0]


@pytest.mark.parametrize('start,unit,date,expected', [
    ('2025-01-31', 'month', '2025-02-28', 1),
    ('2025-01-31', 'month', '2025-03-31', 2),
    ('2024-02-29', 'year', '2025-02-28', 1),
    ('2024-01-01', 'year', '2025-01-01', 1),
])
def test_calendar_date_origins_land_on_exact_round_boundaries(start, unit, date, expected):
    assert to_t(clock(start, unit), date) == expected


def test_monthly_calendar_effects_include_the_last_day_and_average_actual_days():
    c = {'name': 'Monthly closing demand', 'clock': {'rounds': 3, 'unit': 'month', 'start': '2025-01-01'},
         'types': {'firm': {}}, 'patterns': {'closing': {'kind': 'calendar', 'effects': [
             {'on': 'dates', 'dates': ['01-31'], 'effect': 32},
             {'on': 'dates', 'dates': ['02-28'], 'effect': 29},
             {'on': 'dates', 'dates': ['03-31'], 'effect': 32}]}},
         'metrics': {'closing': '$pattern.closing', 'date': '$clock.date'}}
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.series['closing'] == [2, 2, 2]
    assert result.series['date'] == ['2025-01-01', '2025-02-01', '2025-03-01']


@pytest.mark.parametrize('unit', ['month', 'year'])
@pytest.mark.parametrize('t', [-2, -1.5, -.5, 0, .5, 1, 1.5, 3.25])
def test_calendar_moment_and_date_conversion_are_inverses(unit, t):
    c = clock('2024-02-29T12:30:00', unit)
    when = moment(c, t)
    assert to_t(c, when.isoformat()) == pytest.approx(t, abs=1e-10)


def test_multi_month_rounds_cover_the_full_actual_span():
    c = clock('2025-01-31', 'month', step=3)
    first, second = days_covered(c, 0), days_covered(c, 3)
    assert len(first) == 89
    assert len(second) == 92
    assert first[-1] + dt.timedelta(days=1) == second[0] == dt.date(2025, 4, 30)


def test_public_leap_day_yearly_model_restores_without_calendar_failure():
    import json

    c = {'name': 'Leap-day annual plan', 'clock': {'rounds': 3, 'unit': 'year', 'start': '2024-02-29'},
         'types': {'firm': {}}, 'patterns': {
             'month': {'kind': 'seasonal', 'profile': list(range(1, 13))},
             'trend': {'kind': 'trend', 'start': 100, 'slope': 10, 'origin': '2025-02-28'}},
         'metrics': {'month': '$pattern.month', 'trend': '$pattern.trend'}}
    env = fg_env.load(c, seed=11)
    assert env.run(rounds=1).status == 'running'
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.series == {'month': [2, 2, 2], 'trend': [90, 100, 110]}
    assert restored.run().to_dict() == result.to_dict()
