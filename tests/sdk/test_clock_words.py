"""Rounds are named the way a reader counts time, and a narrative tells each moment once, in those terms, with what
happened around it."""
from fg_env import RunResult
from fg_env.analysis import highlights, narrative
from fg_env.sdk.clock_words import period_label, span_label, unit_word

HALF_HOURS = {"mode": "rounds", "unit": "minute", "step": 30, "start": "2026-09-14T08:00"}


def _run(series, events=(), clock=None, rounds=None):
    return RunResult(status="completed", ended_by="rounds", rounds=rounds or len(next(iter(series.values()))), seed=1,
                     arm=None, inputs={}, outputs={"sl": 0.82}, metrics={}, series=series, events=list(events),
                     formats={"sl": "pct"}, clock=clock or HALF_HOURS)


def test_a_round_is_called_by_its_length():
    assert unit_word(HALF_HOURS) == "half-hour"
    assert unit_word({"unit": "minute", "step": 15}) == "quarter-hour"
    assert unit_word({"unit": "day", "step": 7}) == "week"
    assert unit_word({"unit": "hour", "step": 2}) == "2-hour period"
    assert unit_word({"mode": "continuous", "unit": "second"}) == "round"
    assert unit_word({}) == "round"


def test_sub_day_rounds_are_named_by_the_time_they_cover_with_the_day_when_a_run_spans_days():
    assert period_label(HALF_HOURS, 4) == "09:30–10:00"
    assert period_label(HALF_HOURS, 4, rounds=48) == "Mon 14 Sep 09:30–10:00"
    assert span_label(HALF_HOURS, 3, 6) == "09:00–11:00"
    assert period_label({"unit": "week", "start": "2026-01-05"}, 3, capital=True) == "Week 3 (2026-01-19)"
    assert span_label({"unit": "week", "start": "2026-01-05"}, 2, 4) == "weeks 2–4 (2026-01-12 to 2026-01-26)"
    assert period_label({"unit": "minute", "step": 30}, 4) == "half-hour 4"


def test_a_narrative_names_the_time_the_happenings_around_a_move_and_leaves_ordinary_wobbles_out():
    service = [0.9, 0.88, 0.91, 0.2, 0.25, 0.86, 0.9, 0.89, 0.9, 0.91]
    offered = [60, 61, 59, 190, 170, 64, 60, 62, 61, 60]
    run = _run({"service_level": service, "offered": offered},
               events=[{"round": 4, "kind": "news", "text": "The network went down.", "data": {"event": "outage"}},
                       {"round": 4, "kind": "idle", "text": "Ben did not act.", "actor": "ben"}])
    story = narrative(run)
    assert story.startswith("Completed after 10 half-hours, to the end of its clock.")
    line = next(text for text in story.splitlines() if text.startswith("09:30–10:00:"))
    assert "then" in line and "while" in line and line.endswith("around then: The network went down.")
    assert story.count("The network went down.") == 1
    assert "sl 82%" in story
    assert all("round" not in h.text for h in highlights(run, top=5))


def test_a_quiet_run_says_nothing_stood_out_and_a_short_one_still_tells_its_largest_changes():
    quiet = _run({"x": [10, 11] * 15}, clock={"unit": "day"})
    assert "No moment stood out" in narrative(quiet)
    short = narrative(_run({"x": [10, 11, 10, 30]}, clock={"unit": "day"}))
    assert "Too few days to tell unusual moments from usual ones" in short and "Day 4: x rose 20" in short
