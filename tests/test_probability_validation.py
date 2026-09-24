"""Probability authoring errors must not silently become certain or impossible events."""
import pytest

import fg_env


def contract(surface, probability, dynamic=False):
    c = {'name': 'Campaign conversion', 'clock': {'rounds': 1},
         'types': {'worker': {'agent': True, 'policy': 'p'}}, 'entities': {'a': {'type': 'worker'}},
         'world': {'converted': 0}, 'actions': {'attempt': {'by': 'worker', 'do': '$world.converted += 1'}},
         'policies': {'p': {'rules': [{'do': 'attempt'}]}}, 'outputs': {'converted': '$world.converted'}}
    value = probability
    if dynamic:
        c['inputs'] = {'p': {'type': 'bool' if isinstance(probability, bool) else 'number', 'default': probability}}
        value = '$inputs.p'
    if surface == 'action':
        c['actions']['attempt']['chance'] = value
    elif surface == 'policy':
        c['policies']['p']['rules'][0]['chance'] = value
    elif surface == 'expression':
        c['actions']['attempt']['do'] = []
        c['events'] = [{'do': [{'if': f'$chance({value})', 'then': ['$world.converted += 1']}]}]
    return c


@pytest.mark.parametrize('surface,path',
                         [('action', 'actions.attempt.chance'), ('policy', 'policies.p.rules[0].chance')])
@pytest.mark.parametrize('value', [-0.1, 1.1, 80, float('inf'), float('nan'), True])
def test_invalid_literal_probabilities_are_rejected_at_the_authored_field(surface, path, value):
    issues = [i for i in fg_env.check(contract(surface, value), rounds=0) if i.severity == 'error']
    assert any(i.path.startswith(path) for i in issues), issues
    if not isinstance(value, bool):
        assert any(i.fix and '0.8' in i.fix for i in issues)


@pytest.mark.parametrize('surface', ['policy', 'expression'])
@pytest.mark.parametrize('value', [-0.1, 80, True])
def test_dynamic_invalid_probability_fails_instead_of_running_a_different_model(surface, value):
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(contract(surface, value, dynamic=True), seed=3)
    result = failed.value.result
    assert result.status == 'failed', result.to_dict()
    assert result.error
    assert 'chance' in result.error or 'probability' in result.error


@pytest.mark.parametrize('value', [-0.1, 80, True])
def test_dynamic_invalid_action_probability_refuses_the_action_instead_of_running_a_different_model(value):
    result = fg_env.run(contract('action', value, dynamic=True), seed=3)
    assert result.status == 'completed' and result.outputs['converted'] == 0
    finding = next(d for d in result.diagnostics if d['code'] == 'action_rule_failed')
    assert finding['path'] == 'actions.attempt.chance' and 'chance' in finding['message']


@pytest.mark.parametrize('surface', ['action', 'policy', 'expression'])
@pytest.mark.parametrize('value', [0, 1])
def test_probability_boundaries_retain_their_exact_meaning(surface, value):
    result = fg_env.run(contract(surface, value, dynamic=True), seed=3)
    assert result.ok, result.error
    assert result.outputs['converted'] == value


@pytest.mark.parametrize('surface', ['action', 'policy', 'expression'])
def test_fractional_literal_is_valid_and_reproducible(surface):
    c = contract(surface, 0.8)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    a, b = fg_env.run(c, seed=71), fg_env.run(c, seed=71)
    assert a.ok and b.ok
    assert a.to_dict() == b.to_dict()


@pytest.mark.parametrize('seed', [1, 3, 19, 73])
def test_nested_random_probability_preserves_existing_draw_order(seed):
    from fg_env.sampling.seeds import SeedTree
    rng = SeedTree(seed).rng('draws', 'events[0].do', 1, 0)  # the event's own stream in round 1
    roll, probability = rng.random(), rng.random()
    c = {'name': 'Nested draw', 'clock': {'rounds': 1}, 'types': {'item': {}}, 'world': {'outcome': False},
         'events': [{'do': '$world.outcome = $chance($uniform(0, 1))'}], 'outputs': {'outcome': '$world.outcome'}}
    result = fg_env.run(c, seed=seed)
    assert result.ok, result.error
    assert result.outputs['outcome'] == (roll < probability)
