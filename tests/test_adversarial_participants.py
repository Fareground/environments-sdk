"""Participants that misbehave on purpose (_adversaries.py) cannot crash a run, a seat that never acts is reported, and
a call refused for its arguments changes nothing — so a refusal can never be used to learn how luck will fall.
Checked on every example and on generated contracts; ``FG_ENV_SLOW=1`` plays every adversary everywhere."""
import os

import pytest

import fg_env
from fg_env.participants import RandomAgent

import _fuzz
from _adversaries import ADVERSARIES, played, probing
from _leaks import EXAMPLES, SMALL

SLOW = bool(os.environ.get("FG_ENV_SLOW"))
NAMES = list(ADVERSARIES)
#: Each example meets one adversary in turn (all of them when slow).
PAIRS = [(path, name) for k, path in enumerate(EXAMPLES) for name in (NAMES if SLOW else [NAMES[k % len(NAMES)]])]


@pytest.mark.parametrize("path, adversary", PAIRS, ids=lambda value: getattr(value, "stem", value))
def test_no_adversary_crashes_an_example(path, adversary):
    result = fg_env.load(path, seed=1, inputs=SMALL.get(path.stem)).run(ADVERSARIES[adversary], rounds=3)
    assert result.status != "failed", result.error


@pytest.mark.parametrize("seed", range(1000 if SLOW else 20))
def test_no_adversary_crashes_a_generated_contract(seed):
    contract = _fuzz.contract(seed)
    for name, adversary in ADVERSARIES.items():
        result = fg_env.load(contract, seed=seed).run(adversary)
        assert result.status in ("completed", "ended"), (name, result.error)


@pytest.mark.parametrize("adversary", ["refuser", "spammer"])
def test_a_seat_that_tries_but_never_acts_marks_the_run_degraded(adversary):
    lemonade = next(path for path in EXAMPLES if path.stem == "lemonade_stand")
    result = fg_env.load(lemonade, seed=1).run({"ana": ADVERSARIES[adversary], "ben": "random"})
    assert result.status == "completed" and result.degraded and not result.ok


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_a_call_refused_for_its_arguments_changes_nothing_in_an_example(path):
    runs = [fg_env.load(path, seed=1, inputs=SMALL.get(path.stem)).run(agent, rounds=2)
            for agent in (RandomAgent(seed=1), probing(RandomAgent(seed=1)))]
    assert played(runs[1]) == played(runs[0])
