"""The engine's cost follows the work a round does, never its square: an `each` event with a trigger, a crowd whose
actions are checked against an invariant, a run stepped one round at a time.

Each test compares two timings of the same machine in the same process, so it does not depend on how fast the machine
is. The bounds sit far between the two behaviours: the regressions they guard measured several times over them.
"""
import gc
import statistics
import time

import pytest

import fg_env

pytestmark = pytest.mark.slow  # statistical or engine-behaviour: `make test-fast` leaves it out


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
#: about 4 times; converting each event once, about 1. Garbage collection is held off while timing, so a collection
#: that happens to land late (its cost grows with everything the run keeps) is not taken for a slower step.
MOST_SLOWER_LATE = 2.5


def test_stepping_a_run_costs_the_same_late_in_the_run_as_early():
    env = fg_env.load(STEPPED, seed=1)
    steps = []
    gc.disable()
    try:
        for _ in range(600):
            start = time.process_time()
            env.step()
            steps.append(time.process_time() - start)
    finally:
        gc.enable()
    slower = statistics.median(steps[-100:]) / statistics.median(steps[:100])
    assert slower < MOST_SLOWER_LATE, f"a step late in the run took {slower:.1f} times an early one"


def _among_items(items):
    return {"name": "Shop floor", "clock": {"rounds": 3}, "world": {"pot": 0},
            "types": {"p": {"agent": True, "props": {"coins": 10}}, "item": {"props": {"v": 1}}},
            "population": [{"type": "p", "count": 60}, {"type": "item", "count": items}],
            "actions": {"give": {"by": "p", "do": "$world.pot += 1"}}}


#: A turn among 60 agents and 20,000 items that no one may inspect against the same turn without the items: listing
#: what an agent may inspect by scanning every entity on every turn measured about 20 times; by type, about 1.
MOST_SLOWER_AMONG_ITEMS = 3


def test_a_turn_costs_the_same_however_many_entities_it_cannot_inspect():
    slower = _seconds(_among_items(20_000), "random") / _seconds(_among_items(0), "random")
    assert slower < MOST_SLOWER_AMONG_ITEMS, f"20,000 items no agent may inspect made a turn {slower:.1f} times slower"


CHURN = {"name": "Churn", "clock": {"rounds": 1000},
         "types": {"t": {"agent": True, "props": {"cash": 0}}, "tok": {"props": {"v": 0}}},
         "entities": {"t": {"type": "t", "count": 20}},
         "actions": {"work": {"by": "t", "do": ["$actor.cash += 1", {"create": "tok"}, {"remove": "$first(tok)"}]}}}

#: The last 100 of 1,000 rounds in which every action creates one entity and removes another against the first 100:
#: reading a type's living members by passing over every member it ever had measured about 4 times by the end; with
#: the living list kept current, about 1.
MOST_SLOWER_WITH_CHURN = 2


def test_creating_and_removing_entities_in_actions_costs_the_same_late_in_the_run_as_early():
    env = fg_env.load(CHURN, seed=1)
    rounds = []
    gc.disable()
    try:
        for _ in range(1000):
            start = time.process_time()
            env.run(lambda wake: wake.call("work", {}), rounds=1)
            rounds.append(time.process_time() - start)
    finally:
        gc.enable()
    slower = statistics.median(rounds[-100:]) / statistics.median(rounds[:100])
    assert slower < MOST_SLOWER_WITH_CHURN, f"a round late in the run took {slower:.1f} times an early one"
