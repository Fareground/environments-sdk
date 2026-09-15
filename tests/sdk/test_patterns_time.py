"""Time patterns give the values their definitions say, on round and calendar clocks."""
import math

import pytest

from patterns_helpers import series

WEEKS = {"unit": "week", "start": "2025-01-06"}


def test_a_linear_trend_changes_by_its_slope_each_clock_unit_from_round_one():
    got = series({"t": {"kind": "trend", "start": 2, "slope": 0.5}}, {"t": "$pattern.t"}, rounds=4)
    assert got["t"] == [2, 2.5, 3, 3.5]


def test_exponential_and_logistic_trends_follow_their_formulas():
    got = series({"e": {"kind": "trend", "form": "exponential", "start": 100, "rate": 0.1},
                  "s": {"kind": "trend", "form": "logistic", "capacity": 10, "midpoint": 5, "steepness": 1}},
                 {"e": "$pattern.e", "s": "$pattern.s"}, rounds=7)
    assert got["e"] == pytest.approx([100 * math.exp(0.1 * t) for t in range(7)])
    assert got["s"][5] == pytest.approx(5) and got["s"][0] == pytest.approx(10 / (1 + math.exp(5)))


def test_a_trend_counts_clock_units_so_weekly_rounds_of_days_move_seven_at_a_time():
    got = series({"t": {"kind": "trend", "slope": 1, "start": 0}}, {"t": "$pattern.t"}, rounds=3,
                 clock={"unit": "day", "step": 7})
    assert got["t"] == [0, 7, 14]


def test_a_trend_origin_may_be_a_date():
    got = series({"t": {"kind": "trend", "slope": 1, "start": 0, "origin": "2025-01-20"}}, {"t": "$pattern.t"},
                 rounds=4, clock=WEEKS)
    assert got["t"] == [-2, -1, 0, 1]


def test_a_monthly_profile_over_a_year_reads_the_calendar_month():
    profile = [0.5 + i / 10 for i in range(12)]
    got = series({"s": {"kind": "seasonal", "profile": profile}}, {"s": "$pattern.s"}, rounds=9, clock=WEEKS)
    # 2025-01-06 … 2025-02-24: weeks 1-4 are January, 5-8 February, 9 is March
    assert got["s"] == [0.5] * 4 + [0.6] * 4 + [0.7]


def test_a_wave_and_harmonics_repeat_over_a_period_of_rounds():
    got = series({"w": {"kind": "seasonal", "period": 4, "amplitude": 0.2, "peak": 0},
                  "h": {"kind": "seasonal", "period": 4, "harmonics": [[0.1, 0]], "form": "add"}},
                 {"w": "$pattern.w", "h": "$pattern.h"}, rounds=5)
    assert got["w"] == pytest.approx([1.2, 1.0, 0.8, 1.0, 1.2])
    assert got["h"] == pytest.approx([0, 0.1, 0, -0.1, 0])


def test_a_weekday_profile_on_daily_rounds_reads_the_day_of_week():
    got = series({"d": {"kind": "seasonal", "period": "week", "profile": [1, 1, 1, 1, 1.4, 1.8, 0.6]}},
                 {"d": "$pattern.d"}, rounds=7, clock={"unit": "day", "start": "2025-01-06"})
    assert got["d"] == [1, 1, 1, 1, 1.4, 1.8, 0.6]


def test_calendar_effects_apply_per_day_and_a_week_averages_its_days():
    effects = [{"on": "weekend", "effect": 1.5}, {"on": "dates", "dates": ["12-25"], "effect": 2}]
    daily = series({"c": {"kind": "calendar", "effects": effects}}, {"c": "$pattern.c"}, rounds=6,
                   clock={"unit": "day", "start": "2025-12-22"})
    assert daily["c"] == [1, 1, 1, 2, 1, 1.5]
    weekly = series({"c": {"kind": "calendar", "effects": effects}}, {"c": "$pattern.c"}, rounds=1,
                    clock={"unit": "week", "start": "2025-12-22"})
    assert weekly["c"] == [pytest.approx(9 / 7)]


def test_paydays_month_ends_and_nearby_days_are_matched():
    effects = [{"on": "days_of_month", "days": [15], "after": 1, "effect": 1.2},
               {"on": "month_end", "days": 2, "effect": 1.1}]
    got = series({"c": {"kind": "calendar", "effects": effects}}, {"c": "$pattern.c"}, rounds=4,
                 clock={"unit": "day", "start": "2025-01-14"})
    assert got["c"] == [1, 1.2, 1.2, 1]
    end = series({"c": {"kind": "calendar", "effects": effects}}, {"c": "$pattern.c"}, rounds=3,
                 clock={"unit": "day", "start": "2025-01-29"})
    assert end["c"] == [1, 1.1, 1.1]


def test_cycles_follow_their_shape():
    got = series({"q": {"kind": "cycle", "period": 4, "shape": "square"},
                  "z": {"kind": "cycle", "period": 4, "shape": "sawtooth", "level": 10, "amplitude": 2}},
                 {"q": "$pattern.q", "z": "$pattern.z"}, rounds=4)
    assert got["q"] == [1, 1, -1, -1]
    assert got["z"] == pytest.approx([8, 9, 10, 11])


def test_a_lifecycle_ramps_up_after_its_start_then_decays_toward_its_floor():
    got = series({"l": {"kind": "lifecycle", "start": 2, "before": 1, "peak": 2, "ramp": 2, "floor": 1, "half_life": 1}},
                 {"l": "$pattern.l"}, rounds=6)
    assert got["l"] == pytest.approx([1, 1, 1, 1.5, 2, 1.5])


def test_several_launches_multiply_their_drops():
    got = series({"l": {"kind": "lifecycle", "start": [1, 3], "before": 1, "peak": 0.9, "floor": 0.9}},
                 {"l": "$pattern.l"}, rounds=5)
    assert got["l"] == pytest.approx([1, 0.9, 0.9, 0.81, 0.81])


def test_step_changes_apply_in_time_order_and_last():
    got = series({"s": {"kind": "step", "start": 1, "changes": [{"at": 3, "times": 2}, {"at": 2, "to": 5}]}},
                 {"s": "$pattern.s"}, rounds=5)
    assert got["s"] == [1, 1, 5, 10, 10]


def test_a_series_reads_data_by_round_and_holds_its_last_value():
    got = series({"s": {"kind": "series", "data": "$inputs.values"}}, {"s": "$pattern.s"}, rounds=4,
                 inputs={"values": {"type": "list", "default": [3, 4, 5]}})
    assert got["s"] == [3, 4, 5, 5]


def test_a_series_of_dated_rows_per_key_interpolates_between_known_days():
    rows = [{"store": "a", "date": "2025-01-06", "temp": 10}, {"store": "a", "date": "2025-01-20", "temp": 20},
            {"store": "b", "date": "2025-01-06", "temp": 0}]
    got = series({"temp": {"kind": "series", "keys": ["a", "b"], "data": "$inputs.weather", "time": "date",
                           "value": "temp", "match": "store", "missing": "interpolate"}},
                 {"a": "$pattern.temp('a')", "b": "$pattern.temp('b')"}, rounds=4, clock=WEEKS,
                 inputs={"weather": {"type": "table", "default": rows}})
    assert got["a"] == pytest.approx([10, 15, 20, 20]) and got["b"] == [0, 0, 0, 0]


def test_bass_diffusion_gives_adopters_new_adopters_and_the_word_of_mouth_hazard():
    p, q, market = 0.03, 0.4, 1000

    def share(tau):
        decay = math.exp(-(p + q) * tau)
        return (1 - decay) / (1 + (q / p) * decay) if tau > 0 else 0.0

    got = series({"a": {"kind": "diffusion", "p": p, "q": q, "market": market},
                  "n": {"kind": "diffusion", "p": p, "q": q, "market": market, "output": "new"},
                  "h": {"kind": "diffusion", "p": p, "q": q, "output": "hazard"}},
                 {"a": "$pattern.a", "n": "$pattern.n", "h": "$pattern.h(0.5)"}, rounds=4)
    assert got["a"] == pytest.approx([market * share(t) for t in range(4)])
    assert got["n"] == pytest.approx([market * (share(t + 1) - share(t)) for t in range(4)])
    assert got["h"] == [pytest.approx(p + q * 0.5)] * 4


def test_a_named_period_without_a_calendar_start_counts_from_round_one_in_clock_units():
    got = series({"s": {"kind": "seasonal", "period": "year", "amplitude": 1, "peak": 0, "form": "add"}},
                 {"s": "$pattern.s"}, rounds=2, clock={"unit": "month", "step": 6})
    assert got["s"] == pytest.approx([1, -1])
