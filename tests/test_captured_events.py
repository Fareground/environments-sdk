"""Events carried into future rules stay usable and JSON-safe."""
import json

import pytest

import fg_env


def contract(nested=False, second_delay=False):
    body = (['$shock = $batch[0]'] if nested else []) + [
        '$world.cash += $shock.amount',
        '$world.observed = [$shock.kind, $shock.text, $shock.round, $shock.actor, $shock.stage, $shock.time, '
        '$shock.details]']
    if second_delay:
        body = [{'after': 1, 'do': body}]
    return {'name': 'Deferred demand shock', 'clock': {'rounds': 3},
            'types': {'firm': {}}, 'world': {'cash': 100, 'observed': {'type': 'any', 'default': None}},
            'events': [{'at': 1, 'do': [
                {'emit': 'demand_shock', 'say': 'Demand changed',
                 'data': {'amount': 20, 'round': 999, 'details': {'region': 'north', 'units': [2, 3]}}},
                '$shock = $events(demand_shock)[0]', '$batch = [$shock]',
                {'after': 1, 'do': body}]}],
            'outputs': {'cash': '$world.cash', 'observed': '$world.observed'}}


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('second_delay', [False, True])
def test_delayed_event_survives_json_restore_and_independent_fork(nested, second_delay):
    c = contract(nested, second_delay)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=7)
    assert env.run(rounds=1).status == 'running'
    snap = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(c, snap)
    branch = fg_env.fork(c, snap, effects=['$world.cash = 200'])
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'cash': 120, 'observed': [
        'demand_shock', 'Demand changed', 1, None, None, None, {'region': 'north', 'units': [2, 3]}]}
    assert restored.run().to_dict() == result.to_dict()
    branch_result = branch.run()
    assert branch_result.ok, branch_result.error
    assert branch_result.outputs == {**result.outputs, 'cash': 220}


@pytest.mark.parametrize('version', [0, 1, 2, 3, 4])
def test_literal_event_marker_remains_customer_data(version):
    c = contract()
    payload = {'$event': {'kind': 'customer-defined', 'amount': 30}}
    c['world']['payload'] = {'type': 'any', 'default': payload}
    c['events'][0]['do'] = ['$payload = $world.payload',
                           {'after': 1, 'do': ['$world.observed = $payload']}]
    env = fg_env.load(c)
    env.run(rounds=1)
    snap = json.loads(json.dumps(env.snapshot()))
    if version < 4:
        item = snap['scheduled'][0][2]
        item['capture_version'] = version
        item['vars']['payload'] = payload
    result = fg_env.Env.restore(c, snap).run()
    assert result.ok, result.error
    assert result.outputs['observed'] == payload


def test_private_event_exposure_identity_survives_two_restorations():
    c = contract(second_delay=True)
    c['types'] = {'person': {'agent': True}}
    c['entities'] = {'a': {'type': 'person'}, 'b': {'type': 'person'}}
    c['stages'] = [{'name': 'decisions', 'order': '$it.id'}]
    c['actions'] = {'wait': {'by': 'person', 'do': []}}
    c['events'][0]['do'][0]['to'] = ['a']
    c['events'][0]['do'][3]['do'][0]['do'] = [
        '$world.observed = [$seen(a, $shock), $seen(b, $shock)]']
    env = fg_env.load(c)
    updates = {}

    def participant(wake):
        updates[(wake.entity_id, wake.round)] = wake.update
        wake.end()

    for _ in range(2):
        assert env.run(participant, rounds=1).status == 'running'
        env = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run(participant)
    assert result.ok, result.error
    assert result.outputs['observed'] == [True, False]
    assert 'Demand changed' in updates['a', 1]
    assert 'Demand changed' not in updates['b', 1]


@pytest.mark.parametrize('recipients', [None, (), ('a', 'b')])
def test_event_metadata_and_nested_marker_payload_roundtrip(recipients):
    from fg_env.effects.captures import CAPTURE_VERSION, freeze, thaw
    from fg_env.world.parts import LogEvent

    data = {'$event': {'kind': 'literal'}, 'meta': {'$entry': {'author': 'literal'}},
            'round': 999, 'text': 'not the event text'}
    original = LogEvent(7, 2, 'order', '', 'a', recipients, data, 'checkout', 0.0)
    encoded = json.loads(json.dumps(freeze(original)))
    decoded = thaw(encoded, None, version=CAPTURE_VERSION)
    assert isinstance(decoded, LogEvent)
    assert decoded.to_dict() == original.to_dict()
    assert decoded.to == recipients
    assert decoded.expr_attr('round', None) == 2
    assert decoded.expr_attr('text', None) == ''
    assert decoded.expr_attr('meta', None) == data['meta']
    assert decoded.visible_to('a') == (recipients is None or 'a' in recipients)
    assert decoded.visible_to('c') == (recipients is None or 'c' in recipients)
    decoded.data['meta']['$entry']['author'] = 'changed'
    assert original.data['meta']['$entry']['author'] == 'literal'
