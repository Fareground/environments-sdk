"""The Python turn-ending helper can safely follow an automatically ending action."""
import copy

import pytest
from test_turn_limit_feedback import contract

import fg_env


@pytest.mark.parametrize('simultaneous', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
def test_end_after_automatic_completion_is_a_noop_including_recording(simultaneous, terminal):
    c = contract(actions=3 if terminal else 1, simultaneous=simultaneous, terminal=terminal)
    def baseline(w):
        assert w.call('dispatch').ok
    def redundant(w):
        baseline(w)
        assert w.done
        for _ in range(2):
            ended = w.end()
            assert ended.ok and ended.ended
            assert ended.data == {}
    first = fg_env.load(c, seed=1, exposures=True).run(baseline)
    second = fg_env.load(c, seed=1, exposures=True).run(redundant)
    assert first.to_dict() == second.to_dict()
    replay = fg_env.analysis.trace(second).replay(c)
    assert replay.ok, replay.message


def test_repeating_explicit_end_does_not_duplicate_calls_or_settlement():
    c = contract(actions=3)
    def play(w):
        assert w.call('dispatch').ok
        assert w.end().ok
    def redundant(w):
        play(w)
        assert w.end().ok
    a = fg_env.load(c, seed=1, exposures=True).run(play)
    b = fg_env.load(c, seed=1, exposures=True).run(redundant)
    assert a.to_dict() == b.to_dict()


def test_exhausted_calls_can_be_cleaned_up_without_changing_failed_actions():
    c = contract(actions=3, calls=1)
    def play(w):
        assert not w.call('unknown').ok
    def cleanup(w):
        play(w)
        assert w.done and w.end().ok
    a = fg_env.load(c, seed=1, exposures=True).run(play)
    b = fg_env.load(c, seed=1, exposures=True).run(cleanup)
    assert a.to_dict() == b.to_dict()
    assert b.outputs['dispatched'] == 0


def test_raw_tool_calls_after_end_remain_refused():
    def play(w):
        assert w.call('dispatch').ok
        for tool in ('dispatch', 'end_turn'):
            result = w.call(tool)
            assert not result.ok and result.data == {'error': 'ended'}
    assert fg_env.run(contract(), play).ok


def test_timeout_is_not_reported_as_successful_cleanup():
    def play(w):
        w._turn.deadline = 0  # deterministic elapsed-deadline test, no sleeps
        result = w.end()
        assert not result.ok and result.ended
        assert result.data == {'error': 'timeout'}
        assert not w.end().ok
    result = fg_env.run(contract(), play)
    assert result.status == 'completed' and result.outputs['dispatched'] == 0
    assert result.stats['timeouts'] == 1 and result.degraded == ['agents_mostly_failed']  # its one turn ran out of time


def test_externally_closed_turn_retains_its_refusal():
    def play(w):
        w._turn.close()
        result = w.end()
        assert not result.ok and result.data == {'error': 'ended'}
    assert fg_env.run(contract(), play).ok


def test_must_act_and_atomic_validation_are_not_bypassed():
    c = copy.deepcopy(contract(actions=3))
    c['stages'][0].update(must_act=True, valid=['$world.dispatched % 2 == 0'])
    def play(w):
        early = w.end()
        assert not early.ok and not early.ended
        assert w.call('dispatch').ok
        undone = w.end()
        assert not undone.ok and not undone.ended
        assert undone.data == {'error':'undone'}
        assert w.call('dispatch').ok
        assert w.call('dispatch').ok
        assert w.end().ok
        assert w.end().ok
    result = fg_env.run(c, play)
    assert result.ok, result.error
    assert result.outputs['dispatched'] == 2
