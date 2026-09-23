"""A crowd choosing among each other stays affordable: each turn works out its choices once, not once per question.

A turn asks for an entity parameter's choices several times — to know the action is legal, to write its tool, to
check the call, to diagnose a refusal — and used to list every candidate each time. Timing-based, so it runs only
with FG_ENV_SLOW=1: FG_ENV_SLOW=1 pytest tests/test_crowd_scaling.py
"""
import os
import time

import pytest

import fg_env

slow = pytest.mark.skipif(not os.environ.get("FG_ENV_SLOW"), reason="slow verification: set FG_ENV_SLOW=1")

CROWD = 1500
#: A round of random agents choosing among each other, against the same round with an action that takes no
#: argument (both pay for listing everyone in the inspect tool). Listing the choices once per turn measures under 2
#: times; once per question, over 3.
MOST_SLOWER = 2.6


def _crowd(action):
    return {"name": "crowd", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "props": {"w": 0}}},
            "population": [{"type": "p", "count": CROWD, "props": {"w": "$random()"}}],
            "stages": [{"name": "s"}],
            "actions": {"act": action}}


PLAIN = {"by": "p", "do": ["$actor.w += 1"], "terminal": True}
CHOOSING = {"by": "p", "params": {"to": {"type": "entity", "of": "p", "where": "$it.id != $actor.id"}},
            "do": ["$params.to.w += 1"], "terminal": True}


def _seconds(action):
    best = float("inf")
    for _ in range(2):
        env = fg_env.load(_crowd(action), seed=1)
        start = time.process_time()
        env.run("random")
        best = min(best, time.process_time() - start)
    return best


@slow
def test_a_crowd_choosing_among_each_other_lists_the_choices_once_per_turn():
    slower = _seconds(CHOOSING) / _seconds(PLAIN)
    assert slower < MOST_SLOWER, f"choosing among {CROWD} agents made the round {slower:.1f} times slower"
