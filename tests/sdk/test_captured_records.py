"""Deferred record values preserve author access and frozen record content."""
import json

import pytest

import fg_env


def contract(nested=False, second_delay=False):
    body = (["$entry = $batch[0]"] if nested else []) + [
        "$world.seen = $entry.author.cash", "$entry.author.cash += $entry.amount"]
    if second_delay:
        body = [{"after": 1, "do": body}]
    return {"name": "Deferred invoice author", "clock": {"rounds": 3},
            "types": {"firm": {"props": {"cash": 100}}},
            "entities": {"supplier": {"type": "firm"}},
            "world": {"seen": 0},
            "records": {"invoices": {"fields": {"amount": "number"}, "keep": 1}},
            "events": [{"at": 1, "do": [
                {"post": "invoices", "amount": 20, "author": "$entity(supplier)"},
                "$entry = $records(invoices)[0]", "$batch = [$entry]",
                {"after": 1, "do": body},
                {"post": "invoices", "amount": 999, "author": "$entity(supplier)"}]}],
            "outputs": {"seen": "$world.seen", "cash": "$entity(supplier).cash"}}


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('second_delay', [False, True])
def test_record_capture_preserves_author_after_eviction_restore_and_fork(nested, second_delay):
    c = contract(nested, second_delay)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=15)
    assert env.run(rounds=1).status == 'running'
    snap = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(c, snap)
    branch = fg_env.fork(c, snap, effects=['$entity(supplier).cash = 200'])
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': 100, 'cash': 120}
    assert restored.run().to_dict() == result.to_dict()
    result = branch.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': 200, 'cash': 220}


@pytest.mark.parametrize('version', [0, 1, 2, 3])
def test_literal_entry_marker_is_not_reinterpreted(version):
    c = contract()
    payload = {'$entry': {'author': 'supplier', 'amount': 20}}
    c['world']['payload'] = {'type': 'any', 'default': payload}
    c['world']['seen'] = {'type': 'any', 'default': None}
    c['events'][0]['do'] = ['$payload = $world.payload',
                           {'after': 1, 'do': ['$world.seen = $payload']}]
    env = fg_env.load(c)
    env.run(rounds=1)
    snap = json.loads(json.dumps(env.snapshot()))
    if version < 3:
        item = snap['scheduled'][0][2]
        item['capture_version'] = version
        item['vars']['payload'] = payload
    result = fg_env.Env.restore(c, snap).run()
    assert result.ok, result.error
    assert result.outputs['seen'] == payload


def test_anonymous_record_author_remains_none_after_second_restore():
    c = contract(second_delay=True)
    c['events'][0]['do'][0]['author'] = None
    c['events'][0]['do'][3]['do'][0]['do'] = ['$world.seen = $entry.author']
    c['world']['seen'] = {'type': 'any', 'default': 0}
    env = fg_env.load(c)
    for _ in range(2):
        assert env.run(rounds=1).status == 'running'
        env = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': None, 'cash': 100}


def test_immediate_and_delayed_record_rules_have_same_business_result():
    delayed = contract()
    immediate = contract()
    immediate['events'][0]['do'][3:4] = immediate['events'][0]['do'][3]['do']
    results = [fg_env.load(c).run() for c in (immediate, delayed)]
    assert all(r.ok for r in results)
    assert results[0].outputs == results[1].outputs == {'seen': 100, 'cash': 120}
