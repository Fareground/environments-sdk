"""Observing both evaluators must preserve lazy random-stream use and the exact next draw."""
from types import SimpleNamespace

from expr_dual import _capture, _left_behind, _restore

from fg_env.sampling.seeds import SeedTree


def test_capture_does_not_initialize_an_unused_stream():
    stream = SeedTree(17).lazy_rng('stage')
    world = SimpleNamespace(rng=stream)
    saved = _capture(world)
    assert not stream.drawn
    _restore(world, saved)
    assert not stream.drawn


def test_restore_rewinds_first_use_and_compares_independent_backing_streams():
    seeds = SeedTree(17)
    stream = seeds.lazy_rng('stage')
    world = SimpleNamespace(rng=stream)
    before = _capture(world)
    expected = seeds.rng('stage')
    first, second = expected.random(), expected.random()
    assert stream.random() == first
    old_after = _capture(world)
    _restore(world, before)
    assert not stream.drawn
    assert stream.random() == first
    new_after = _capture(world)
    assert _left_behind(old_after) == _left_behind(new_after)
    assert stream.random() == second
    assert _left_behind(_capture(world)) != _left_behind(new_after)


def test_restore_preserves_an_already_used_stream_and_its_next_draw():
    seeds = SeedTree(17)
    stream = seeds.lazy_rng('stage')
    expected = seeds.rng('stage')
    assert stream.random() == expected.random()
    world = SimpleNamespace(rng=stream)
    before = _capture(world)
    backing = stream._stream
    next_draw = expected.random()
    assert stream.random() == next_draw
    _restore(world, before)
    assert stream.drawn and stream._stream is backing
    assert stream.random() == next_draw
