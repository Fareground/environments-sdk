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


#: Sealed bids attached to a declared stage written as sequential, with every collector's cash on show: the bids are
#: held until everyone has chosen, so no bid moves what a later bidder sees (audit 14 mech H2).
SEALED_ON_A_DECLARED_STAGE = [
    {"name": "Sealed", "types": {"collector": {"agent": True, "props": {"cash": 500}}},
     "entities": {"collector": {"type": "collector", "count": 3}},
     "views": {"standings": {"of": "collector", "show": "{$it.name}: {$it.cash} cash"}},
     "mechanisms": {"art": {"kind": "market", "mode": "auction", "format": fmt, "who": "collector",
                            "item": "a painting", "stage": "bids"}},
     "stages": [{"name": "bids", "turns": "sequential"}], "clock": {"rounds": 3}}
    for fmt in ("first_price", "second_price", "uniform")]


@pytest.mark.parametrize("contract", SEALED_ON_A_DECLARED_STAGE, ids=lambda c: c["mechanisms"]["art"]["format"])
def test_a_sealed_bid_on_a_declared_stage_changes_nothing_another_bidder_sees(contract):
    """Another bidder's sealed bid changes nothing a bidder sees before it bids — not the world (a bid's escrowed
    cash would be a reveal of its amount, which the comparison of shown texts alone would take for the rules'), and
    not what it is shown."""
    for observer in ("collector_2", "collector_3"):
        first = play(contract, 1, observer)
        second = play(contract, 1, observer, replay=first, sealed=True)  # the others bid afresh
        before, after = first.turns[0], second.turns[0]  # its first bid, after the others' in turn order
        assert before.seen == after.seen and before.shown == after.shown, observer
        assert not leaks(first, second, observer)
