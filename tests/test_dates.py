"""Calendar functions, a clock start read from an input, and rounds named in the clock's unit."""
import pytest

import fg_env


def _eval(source):
    return fg_env.load({"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                        "outputs": {"value": {"type": "any", "expr": source}}}, seed=1).run().outputs["value"]


@pytest.mark.parametrize("source, expected", [
    ("$date_add('2026-09-14', 3)", "2026-09-17"),
    ("$date_add('2026-09-14', 2, week)", "2026-09-28"),
    ("$date_add('2026-01-31', 1, month)", "2026-02-28"),
    ("$date_add('2024-02-29', 1, year)", "2025-02-28"),
    ("$date_add('2026-11-15', 1, quarter)", "2027-02-15"),
    ("$date_add('2026-09-14', -14, days)", "2026-08-31"),
    ("$date_add('2026-09-14T09:30', 90, minute)", "2026-09-14T11:00"),
    ("$date_add('2026-09-14', 1.5, hour)", "2026-09-14T01:30"),
    ("$days_between('2026-09-01', '2026-09-15')", 14),
    ("$days_between('2026-09-15', '2026-09-01')", -14),
    ("$days_between('2026-09-14T06:00', '2026-09-15')", 0.75),
    ("$date_part('2026-09-14', weekday)", 1),
    ("$date_part('2026-09-20', weekday_name)", "Sunday"),
    ("$date_part('2026-09-14', week)", 38),
    ("$date_part('2026-09-14', quarter)", 3),
    ("$date_part('2026-09-14', month_name)", "September"),
    ("$date_part('2026-12-31', day_of_year)", 365),
    ("$date_part('2026-09-14T09:30', hour)", 9),
    ("$is_holiday('2026-12-25', ['2026-12-25', '2027-01-01'])", True),
    ("$is_holiday('2026-12-24T10:00', [{date: '2026-12-25'}])", False),
])
def test_calendar_functions_move_and_take_apart_iso_dates(source, expected):
    assert _eval(source) == expected


@pytest.mark.parametrize("source, message", [
    ("$date_add('14/09/2026', 1)", "must be an ISO date like 2026-09-14"),
    ("$date_add('2026-09-14', 1, fortnight)", "unit must be one of day, week"),
    ("$date_add('2026-09-14', 0.5, month)", "moving by months takes a whole number"),
    ("$date_part('2026-09-14', season)", "part must be one of year, quarter"),
    ("$is_holiday('2026-09-14', [3])", "holidays must be ISO date texts or rows with a date field"),
])
def test_calendar_mistakes_say_what_to_pass(source, message):
    result = fg_env.load({"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                          "events": [{"do": [f"$value = {source}"]}]}, seed=1).run()
    assert result.status == "failed" and message in result.error


WEEKLY = {"name": "Weekly shop", "clock": {"rounds": 4, "unit": "week", "start": "$inputs.start"},
          "inputs": {"start": {"type": "date", "default": "2026-08-31"}},
          "types": {"t": {"props": {"v": 0}}},
          "world": {"sold": 10},
          "events": [{"phase": "start", "do": ["$world.sold = 80 if $round == 3 else 10"]}],
          "outputs": {"first": "$clock.start", "week": "$date_part($clock.date, week)",
                      "sold": {"expr": "$world.sold", "series": True}}}


def test_clock_start_may_come_from_an_input_so_each_case_carries_its_own_dates():
    assert [i for i in fg_env.check(WEEKLY) if i.severity == "error"] == []
    result = fg_env.run(WEEKLY, seed=1, inputs={"start": "2026-01-05"})
    assert result.outputs == {"first": "2026-01-05", "week": 5, "sold": 10}  # week 4 of the run starts 2026-01-26, ISO week 5
    assert fg_env.run(WEEKLY, seed=1).outputs["first"] == "2026-08-31"


def test_a_clock_start_input_that_is_not_a_date_fails_the_load_at_clock_start():
    contract = {**WEEKLY, "inputs": {"start": {"type": "text", "default": "soon"}}}
    with pytest.raises(fg_env.RunError) as excinfo:
        fg_env.load(contract, seed=1)
    assert excinfo.value.path == "clock.start" and "ISO date" in str(excinfo.value)


def test_a_clock_start_expression_may_only_read_inputs():
    contract = {**WEEKLY, "clock": {"rounds": 4, "unit": "week", "start": "$world.sold"}}
    [issue] = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert issue.path == "clock.start" and "$world is not available here" in issue.message


def test_narratives_and_summaries_name_rounds_in_the_clock_unit_with_dates():
    result = fg_env.run(WEEKLY, seed=1)
    story = fg_env.analysis.narrative(result)
    assert story.startswith("Completed after 4 weeks")
    assert "Week 3 (2026-09-14): sold rose 70 (+700%), then gave back all of it by week 4 (2026-09-21)" in story
    assert result.summary().startswith("completed after 4 weeks")
    assert result.period(2) == "week 2 (2026-09-07)"


def test_a_saved_result_keeps_its_clock_and_an_older_one_counts_rounds(tmp_path):
    result = fg_env.run(WEEKLY, seed=1)
    result.save(tmp_path / "run.json")
    assert fg_env.RunResult.load(tmp_path / "run.json").period(1, capital=True) == "Week 1 (2026-08-31)"
    older = fg_env.RunResult(status="completed", ended_by=None, rounds=2, seed=1, arm=None, inputs={}, outputs={},
                             metrics={}, series={})
    assert older.period(2) == "round 2"
