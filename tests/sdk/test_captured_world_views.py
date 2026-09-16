"""Shared-state aliases in delayed rules belong to the executing run."""
import json

import pytest

import fg_env


def contract(root, nested=False):
    field = 'round' if root == 'clock' else 'balance'
    reference = '$current' if nested else '$alias'
    body = (['$current = $aliases[0]'] if nested else []) + [f'$world.seen = {reference}.{field}']
    if root != 'clock':
        body.append(f'{reference}.balance += 20')
    c = {'name': 'Deferred state alias', 'clock': {'rounds': 3}, 'types': {'firm': {}},
         'world': {'balance': 100, 'seen': {'type': 'any', 'default': None}},
         'events': [{'at': 1, 'do': [f'$alias = ${root}', '$aliases = [$alias]', {'after': 1, 'do': body}]}],
         'outputs': {'seen': '$world.seen', 'balance': f'${root}.balance' if root != 'clock' else '$world.balance'}}
    if root == 'physics':
        c['physics'] = {'vars': {'balance': {'start': 100, 'rate': '0'}}}
    return c


@pytest.mark.parametrize('root', ['world', 'physics', 'clock'])
@pytest.mark.parametrize('nested', [False, True])
def test_delayed_aliases_have_json_snapshots_and_exact_continuation(root, nested):
    c = contract(root, nested)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=15)
    assert env.run(rounds=1).status == 'running'
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': 2 if root == 'clock' else 100, 'balance': 100 if root == 'clock' else 120}
    assert result.to_dict() == restored.run().to_dict()


@pytest.mark.parametrize('root', ['world', 'physics', 'clock'])
def test_forked_alias_reads_branch_state_not_original_run(root):
    c = contract(root)
    env = fg_env.load(c)
    env.run(rounds=1)
    effects = [] if root == 'clock' else [f'${root}.balance = 200']
    branch = fg_env.fork(c, env.snapshot(), effects=effects)
    result = branch.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': 2 if root == 'clock' else 200, 'balance': 100 if root == 'clock' else 220}
    assert env.run().outputs == {'seen': 2 if root == 'clock' else 100, 'balance': 100 if root == 'clock' else 120}


@pytest.mark.parametrize('version', [0, 1, 2])
@pytest.mark.parametrize('name', ['world', 'physics', 'clock', 'external'])
def test_literal_view_marker_data_remains_literal_in_all_capture_versions(version, name):
    c = contract('world')
    c['world']['payload'] = {'type': 'any', 'default': {'$view': name}}
    c['events'][0]['do'] = ['$payload = $world.payload', {'after': 1, 'do': ['$world.seen = $payload']}]
    env = fg_env.load(c)
    env.run(rounds=1)
    snapshot = json.loads(json.dumps(env.snapshot()))
    if version < 2:
        item = snapshot['scheduled'][0][2]
        item['capture_version'] = version
        item['vars']['payload'] = {'$view': name}
    result = fg_env.Env.restore(c, snapshot).run()
    assert result.ok, result.error
    assert result.outputs['seen'] == {'$view': name}


@pytest.mark.parametrize('root', ['world', 'physics', 'clock'])
def test_aliases_survive_a_second_delay_and_a_second_snapshot(root):
    c = contract(root)
    effects = c['events'][0]['do'][-1]['do']
    c['events'][0]['do'][-1]['do'] = [{'after': 1, 'do': effects}]
    env = fg_env.load(c, seed=15)
    for _ in range(2):
        env.run(rounds=1)
        env = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': 3 if root == 'clock' else 100, 'balance': 100 if root == 'clock' else 120}
