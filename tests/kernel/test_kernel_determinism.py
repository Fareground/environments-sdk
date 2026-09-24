"""Determinism: a seed decides a run. Played twice, or with its sealed turns on worker threads instead of inline, the
same seed gives the same event log (by hash), the same result and the same statistics."""
import pytest
from _corpus import (
    EXAMPLES,
    FAST_EXAMPLES,
    FUZZ_FAST,
    FUZZ_SLOW,
    SEEDS_FAST,
    SEEDS_SLOW,
    ConcurrentRandom,
    clean_seed,
    events_sha256,
    load,
)

ROUNDS = 3


def _played(subject, seed, rounds, parallel):
    env = load(subject, seed, parallel=parallel)
    result = env.run(ConcurrentRandom(seed=seed), rounds=rounds)
    assert result.status != "failed", result.error
    return events_sha256(result), result.stats, result.to_dict()


def _same_every_way(subject, seed, rounds=ROUNDS):
    first = _played(subject, seed, rounds, parallel=8)
    again = _played(subject, seed, rounds, parallel=8)
    inline = _played(subject, seed, rounds, parallel=1)
    for other in (again, inline):
        assert other[0] == first[0], "event logs differ"
        assert other[1] == first[1], "statistics differ"
        assert other[2] == first[2], "results differ"


@pytest.mark.parametrize("seed", SEEDS_FAST)
@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_an_example_plays_the_same_twice_and_on_threads_as_inline(path, seed):
    _same_every_way(path, seed)


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_a_generated_contract_plays_the_same_twice_and_on_threads_as_inline(fuzz):
    _same_every_way(clean_seed(fuzz), seed=fuzz)


@pytest.mark.slow
@pytest.mark.parametrize("seed", SEEDS_SLOW)
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_plays_the_same_every_way_under_many_seeds(path, seed):
    _same_every_way(path, seed, rounds=None)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_many_generated_contracts_play_the_same_every_way(fuzz):
    _same_every_way(clean_seed(fuzz), seed=fuzz, rounds=None)
