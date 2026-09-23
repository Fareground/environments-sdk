"""The engine's cost follows the work a round does, never its square: an `each` event with a trigger, a crowd whose
actions are checked against an invariant, a run stepped one round at a time.

Each test compares two timings of the same machine in the same process, so it does not depend on how fast the machine
is. The bounds sit far between the two behaviours: the regressions they guard measured several times over them.
"""
import statistics
import time

import fg_env


def _seconds(contract, participants, rounds=None):
    best = float("inf")
    for _ in range(2):
        env = fg_env.load(contract, seed=1)
        start = time.process_time()
        result = env.run(participants, rounds=rounds)
        best = min(best, time.process_time() - start)
        assert result.status in ("completed", "ended", "running"), result.error
    return best


def _things(count, trigger):
    contract = {"name": "Decay", "clock": {"rounds": 1},
                "world": {"total": 0.0},
                "types": {"thing": {"props": {"v": 1.0}}},
                "population": [{"type": "thing", "count": count}],
                "events": [{"phase": "end", "each": "thing", "do": ["$it.v = $it.v * 0.99 + 0.01"]}],
                "invariants": ["$all(thing, $it.v >= 0)"]}
    if trigger:
        contract["triggers"] = [{"when": "$world.total < 0", "do": []}]
    return contract


#: An `each` event over 3,000 items with an invariant and an unrelated trigger measured 40 times the same event
#: without the trigger when the invariant was re-checked after every item; checked once the event is whole, about 1.
MOST_SLOWER_WITH_A_TRIGGER = 3


def test_an_each_event_costs_about_the_same_with_a_trigger_as_without():
    slower = _seconds(_things(3000, trigger=True), None) / _seconds(_things(3000, trigger=False), None)
    assert slower < MOST_SLOWER_WITH_A_TRIGGER, f"a trigger made the each event {slower:.1f} times slower"


def _capped(count):
    return {"name": "Spenders", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "policy": "spend", "props": {"cash": 100.0}}},
            "population": [{"type": "p", "count": count}],
            "invariants": ["$all(p, $it.cash >= 0)"],
            "actions": {"spend": {"by": "p", "do": "$actor.cash -= 1"}},
            "policies": {"spend": {"rules": [{"do": "spend"}]}}}


#: A turn in a crowd of 2,400 against a turn in a crowd of 150, each action checked against an invariant over the
#: whole crowd: re-evaluated whole after every action it measured about 5 times; re-checked for what changed, about 1.
MOST_SLOWER_PER_TURN = 2


def test_a_crowd_turn_checked_against_an_invariant_costs_the_same_at_any_crowd_size():
    small, large = 150, 2400
    slower = (_seconds(_capped(large), None) / large) / (_seconds(_capped(small), None) / small)
    assert slower < MOST_SLOWER_PER_TURN, f"a turn among {large} agents took {slower:.1f} times a turn among {small}"


STEPPED = {"name": "Chatter", "clock": {"rounds": 600},
           "types": {"p": {"agent": True, "policy": "talk", "props": {"said": 0}}},
           "population": [{"type": "p", "count": 40}],
           "actions": {"talk": {"by": "p", "do": "$actor.said += 1", "announce": "{$actor.name} talks."}},
           "policies": {"talk": {"rules": [{"do": "talk"}]}}}

#: The last 100 of 600 `step()` calls against the first 100: rebuilding every result's whole event log measured
#: about 4 times; converting each event once, about 1.
MOST_SLOWER_LATE = 1.6


def test_stepping_a_run_costs_the_same_late_in_the_run_as_early():
    env = fg_env.load(STEPPED, seed=1)
    steps = []
    for _ in range(600):
        start = time.process_time()
        env.step()
        steps.append(time.process_time() - start)
    slower = statistics.median(steps[-100:]) / statistics.median(steps[:100])
    assert slower < MOST_SLOWER_LATE, f"a step late in the run took {slower:.1f} times an early one"
