"""Noninterference: changing only what an agent must not know changes nothing it is shown, offered or charged (see
``_noninterference.py``), over the fuzzed contracts — with whispers and tools that turn on hidden values added
(``_fuzz.secretive``) — and the examples. The default run takes a small sample; the slow variants take every clean
fuzz contract of the slow range and every example."""
import _fuzz
import pytest
from _corpus import EXAMPLES, FUZZ_FAST, FUZZ_SLOW, agent_ids
from _leaks import SMALL
from _noninterference import leaks, play

import fg_env

#: Examples for the default run: hidden hands, roles, sealed bids, private values, whispers.
FAST = [path for path in EXAMPLES if path.stem in {"kuhn_poker", "werewolf", "auction_house", "diplomacy"}]
#: Observers per contract: the default run's, and the slow variants'.
OBSERVERS_FAST, OBSERVERS_SLOW = 2, 4
#: Rounds an example plays at most.
ROUNDS = 3


def source(subject):
    """A contract to load: an example path as it is, a fuzz seed as its secretive contract."""
    return subject if hasattr(subject, "stem") else _fuzz.secretive(subject)


def clean_seed(seed):
    """``seed``, when its secretive contract checks clean; otherwise the test is skipped."""
    if any(issue.severity == "error" for issue in fg_env.check(_fuzz.secretive(seed), rounds=0)):
        pytest.skip("the generated contract does not check clean")
    return seed


def _observers(subject, count):
    env = fg_env.load(source(subject), seed=1, inputs=_inputs(subject))
    agents = sorted(agent_ids(env))
    return agents[:: max(1, len(agents) // count)][:count]


def _inputs(subject):
    return SMALL.get(subject.stem) if hasattr(subject, "stem") else None


def _noninterference(subject, observers, seed=1):
    rounds = ROUNDS if hasattr(subject, "stem") else None
    found = []
    for observer in _observers(subject, observers):
        first = play(source(subject), seed, observer, _inputs(subject), rounds=rounds)
        for sealed in (False, True):
            second = play(source(subject), seed, observer, _inputs(subject), replay=first, rounds=rounds, sealed=sealed)
            assert second.status == first.status, (observer, sealed, second.status, first.status)
            found += leaks(first, second, observer)
    assert not found, "\n".join(found)


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_what_an_agent_must_not_know_changes_nothing_it_is_shown(fuzz):
    _noninterference(clean_seed(fuzz), OBSERVERS_FAST)


@pytest.mark.parametrize("path", FAST, ids=[p.stem for p in FAST])
def test_what_an_agent_must_not_know_in_an_example_changes_nothing_it_is_shown(path):
    _noninterference(path, OBSERVERS_FAST)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_what_an_agent_must_not_know_in_many_contracts_changes_nothing_it_is_shown(fuzz):
    _noninterference(clean_seed(fuzz), OBSERVERS_SLOW)


@pytest.mark.slow
@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_what_an_agent_must_not_know_in_every_example_changes_nothing_it_is_shown(path):
    _noninterference(path, OBSERVERS_SLOW)
