"""Game states and copies nobody holds any more: garbage collection discards them without doing their run's work.

Collection runs on whatever thread allocates, in the middle of whatever that thread evaluates. A discarded run must
not evaluate rules or charge budgets there, and must not wait for a thread there; a copy's thread stops on its own.

A longer run over more threads and time: FG_ENV_SLOW=1 pytest tests/test_collected_copies.py
"""
import gc
import os
import random
import threading
import time
import weakref

import pytest

from fg_env.expr import base as expr_base
from fg_env.actions.book import ActionBook
from fg_env.errors import RunError
from fg_env.game import apply_step, game, random_step
from fg_env.game.runs import ThreadedRun
from fg_env.copying.stepping import Stepper
from game_contracts import GAMES, load_game

SLOW = bool(os.environ.get("FG_ENV_SLOW"))


@pytest.fixture
def watched(monkeypatch):
    """Rules evaluated and threads joined, each with the thread it happened on."""
    seen = {"rules": [], "joins": []}
    blocked, join = ActionBook.blocked, threading.Thread.join

    def counting_blocked(self, *args, **kwargs):
        seen["rules"].append(threading.current_thread())
        return blocked(self, *args, **kwargs)

    def counting_join(self, *args, **kwargs):
        seen["joins"].append(threading.current_thread())
        return join(self, *args, **kwargs)

    monkeypatch.setattr(ActionBook, "blocked", counting_blocked)
    monkeypatch.setattr(threading.Thread, "join", counting_join)
    return seen


def _dropped_inside_an_evaluation(held, seen):
    """Drop the only reference to the state in ``held``, then collect inside a shared budget; what it charged."""
    gc.collect()
    seen["rules"].clear()
    seen["joins"].clear()
    gc.disable()
    try:
        held.clear()
        with expr_base.shared_budget(1000, "outer"):
            gc.collect()
            return expr_base._BUDGET.used
    finally:
        gc.enable()


def _waiting_twin(stepped):
    """A state at the first decision, and a clone of it one decision on (held only by the returned list)."""
    subject = game(GAMES / "tic_tac_toe.json", seed=1)
    subject._stepped = stepped
    root = subject.new_initial_state()
    twin = root.clone()
    twin.apply_action(twin.legal_actions()[0])
    return root, [twin]


def test_a_stepped_state_collected_inside_an_evaluation_evaluates_no_rule_and_charges_nothing(watched):
    root, held = _waiting_twin(stepped=True)
    run = held[0]._run
    assert isinstance(run, Stepper) and run.pause is not None
    env = weakref.ref(run._run())
    del run
    charged = _dropped_inside_an_evaluation(held, watched)
    assert env() is None  # the run was collected
    assert charged == 0
    assert watched["rules"] == []
    root.close()


def test_a_piloted_state_collected_inside_an_evaluation_only_asks_its_thread_to_stop(watched):
    root, held = _waiting_twin(stepped=False)
    assert isinstance(held[0]._run, ThreadedRun)
    branch, thread = weakref.ref(held[0]._run._branch), held[0]._run._pilot._thread
    here = threading.current_thread()
    charged = _dropped_inside_an_evaluation(held, watched)
    assert branch() is None  # the copy was collected
    assert charged == 0
    assert here not in watched["rules"] and here not in watched["joins"]
    thread.join(timeout=10)
    assert not thread.is_alive()
    root.close()


def test_closing_a_piloted_state_stops_its_thread_before_returning():
    root, held = _waiting_twin(stepped=False)
    twin = held.pop()
    thread = twin._run._pilot._thread
    twin.close()
    assert not thread.is_alive()
    root.close()


def test_a_stepped_state_that_fails_while_a_turn_waits_counts_that_turn_like_a_piloted_one():
    broken = load_game("nim")
    broken["invariants"] = [{"expr": "$world.stones == $inputs.stones", "why": "nobody may take a stone"}]
    results = []
    for stepped in (True, False):
        subject = game(broken)
        subject._stepped = stepped
        state = subject.new_initial_state()
        with pytest.raises(RunError, match="nobody may take a stone"):
            state.apply_action(state.legal_actions()[0])
        results.append(state._run.read(lambda env: env.result().to_dict()))
        state.close()
    assert results[0] == results[1]


def _drop_states(seed, until, errors):
    rng = random.Random(seed)
    try:
        subjects = []
        for name in ("leduc_poker", "pig", "tic_tac_toe", "biased_pennies"):
            stepped, piloted = game(GAMES / f"{name}.json", seed=seed % 5), game(GAMES / f"{name}.json", seed=seed % 5)
            piloted._stepped = False
            subjects += [stepped, piloted]
        while time.monotonic() < until:
            state = rng.choice(subjects).new_initial_state()
            kept = []
            for _ in range(40):
                if state.is_terminal():
                    break
                twin = state.clone()
                apply_step(twin, random_step(twin, rng))
                kept.append(twin)
                if len(kept) > 2:
                    kept.pop(rng.randrange(len(kept)))  # dropped without closing
                apply_step(state, random_step(state, rng))
            if rng.random() < 0.5:
                state.close()
    except BaseException as exc:  # reported by the test on the main thread
        errors.append(exc)


def test_states_dropped_without_closing_on_several_threads_under_constant_collection_play_on_correctly():
    seconds, threads = (60.0, 6) if SLOW else (3.0, 3)
    thresholds = gc.get_threshold()
    gc.set_threshold(5, 1, 1)
    errors = []
    try:
        until = time.monotonic() + seconds
        pool = [threading.Thread(target=_drop_states, args=(index, until, errors)) for index in range(threads)]
        for thread in pool:
            thread.start()
        for thread in pool:
            thread.join()
    finally:
        gc.set_threshold(*thresholds)
    gc.collect()
    assert errors == []
