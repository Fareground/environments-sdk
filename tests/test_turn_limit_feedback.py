"""A post-turn refusal explains exhausted allowances without changing execution."""
import pytest

import fg_env


def contract(actions=1, calls=8, simultaneous=False, terminal=False):
    return {'name': 'Dispatch allowance', 'clock': {'rounds': 1},
            'types': {'dispatcher': {'agent': True}}, 'entities': {'hub': {'type': 'dispatcher'}},
            'world': {'dispatched': 0},
            'actions': {'dispatch': {'by': 'dispatcher', 'terminal': terminal, 'do': '$world.dispatched += 1'}},
            'stages': [{'name': 'dispatching', 'actions': ['dispatch'], 'max_actions': actions,
                        'max_calls': calls, 'turns': 'simultaneous' if simultaneous else 'sequential'}],
            'outputs': {'dispatched': '$world.dispatched'}}


@pytest.mark.parametrize('simultaneous', [False, True])
@pytest.mark.parametrize('allowance', [1, 2])
def test_exhausted_action_allowance_names_stage_and_limit(simultaneous, allowance):
    seen = []

    def play(w):
        for _ in range(allowance):
            assert w.call('dispatch').ok
        refusal = w.call('dispatch')
        assert not refusal.ok and refusal.ended
        assert refusal.data == {'error': 'ended'}
        assert 'dispatching' in refusal.text
        assert f'{allowance} action' in refusal.text
        assert 'per turn' in refusal.text
        seen.append(refusal.text)

    result = fg_env.run(contract(actions=allowance, simultaneous=simultaneous), play)
    assert result.status == 'completed', result.error
    assert result.outputs['dispatched'] == allowance
    assert len(seen) == 1


def test_exhausted_call_allowance_is_distinct_from_actions():
    def play(w):
        assert not w.call('unknown').ok
        assert not w.call('unknown').ok
        refusal = w.call('dispatch')
        assert not refusal.ok and refusal.ended
        assert '2 tool calls' in refusal.text
        assert 'dispatching' in refusal.text
        assert refusal.data == {'error': 'ended'}
    result = fg_env.run(contract(actions=4, calls=2), play)
    assert result.status == 'completed', result.error
    assert result.outputs['dispatched'] == 0


@pytest.mark.parametrize('terminal', [False, True])
def test_voluntary_or_terminal_ending_does_not_claim_exhausted_allowances(terminal):
    def play(w):
        assert w.call('dispatch' if terminal else 'end_turn').ok
        refusal = w.call('dispatch')
        assert not refusal.ok and refusal.ended
        assert 'allows' not in refusal.text
        assert refusal.data == {'error': 'ended'}
    result = fg_env.run(contract(actions=4, terminal=terminal), play)
    assert result.status == 'completed', result.error
    assert result.outputs['dispatched'] == int(terminal)
