"""Shared-record candidates preserve the complete live scan's semantics."""
import json

import pytest

import fg_env
from fg_env.sdk.errors import RunError
from fg_env.sdk.record_index import equality_fields
from fg_env.sdk.run_copy import _copy_world


def contract(rule='$viewer.team == $it.team', keep=None):
    record = {'fields': {'team': 'int', 'value': 'int'}, 'visible': rule}
    if keep is not None:
        record['keep'] = keep
    return {'name': 'Shared record access', 'clock': {'rounds': 2},
            'world': {'shared': False},
            'types': {'reader': {'agent': True, 'props': {'team': 0}}},
            'entities': {'a': {'type': 'reader'}, 'b': {'type': 'reader', 'props': {'team': 1}}},
            'records': {'notes': record, 'public': {'fields': {'value': 'int'}}}}


def post(w, team, value, to=None):
    return w.post('notes', {'team': team, 'value': value}, 'a', to, 'probe')


def read(w, who):
    viewer = w.entities[who]
    rows = w.visible_records('notes', viewer)
    assert rows == [r for r in w.records('notes') if w.entry_visible('notes', r, viewer)]
    events = w.events('record', viewer)
    assert events == [e for e in w.log if e.kind == 'record' and w.event_visible(e, viewer)]
    return [r['value'] for r in rows], [e.data.get('fields', {}).get('value') for e in events]


@pytest.mark.parametrize('rule', ['$viewer.team == $it.team', '$it.team == $viewer.team',
                                  '  $viewer.team\t== $it.team  '])
def test_current_team_controls_all_retained_history(rule):
    w = fg_env.load(contract(rule)).world
    for i in range(6):
        post(w, i % 2, i)
    assert read(w, 'a') == ([0, 2, 4], [0, 2, 4])
    assert read(w, 'b') == ([1, 3, 5], [1, 3, 5])
    w.set_prop(w.entities['a'], 'team', 1)
    assert read(w, 'a') == read(w, 'b') == ([1, 3, 5], [1, 3, 5])


def test_retention_rollback_restores_rows_order_and_reused_sequences():
    w = fg_env.load(contract(keep=3)).world
    for i in range(3):
        post(w, i % 2, i)
    mark = w.journal.mark()
    w.set_prop(w.entities['a'], 'team', 1)
    for i in range(3, 8):
        post(w, 1, i)
    assert read(w, 'a') == ([5, 6, 7], [5, 6, 7])
    w.journal.rollback(mark)
    assert read(w, 'a') == ([0, 2], [0, 2])
    assert read(w, 'b') == ([1], [1])
    post(w, 0, 8)
    assert read(w, 'a') == ([2, 8], [2, 8])


@pytest.mark.parametrize('keep', [None, 2])
def test_restore_copy_and_fork_rebuild_for_new_policy(keep):
    c = contract(keep=keep)
    env = fg_env.load(c)
    for i in range(4):
        post(env.world, i % 2, i)
    worlds = [fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot()))).world,
              _copy_world(env.world), env.fork().world]
    for w in worlds:
        assert read(w, 'a') == read(env.world, 'a')
        assert read(w, 'b') == read(env.world, 'b')
        w.set_prop(w.entities['a'], 'team', 1)
        assert read(w, 'a') == read(env.world, 'b')
        post(w, 1, 9)
        assert read(w, 'a')[0][-1] == 9
        assert 9 not in read(env.world, 'b')[0]
    forked = env.fork(patch={'records': {'notes': {'visible': 'all'}}})
    assert read(forked.world, 'a')[0] == ([2, 3] if keep else [0, 1, 2, 3])
    reindexed = forked.fork(patch={'records': {'notes': {'visible': '$viewer.team == $it.team'}}})
    assert read(reindexed.world, 'a') == read(env.world, 'a')


def test_recipients_and_mixed_public_notifications_remain_ordered():
    w = fg_env.load(contract()).world
    post(w, 0, 1, to=('b',))  # Author can read row, but is not an event recipient.
    w.post('public', {'value': 2}, 'b', None, 'probe')
    post(w, 1, 3, to=('a',))  # Team b is excluded by explicit recipients.
    assert read(w, 'a') == ([1], [2])
    assert read(w, 'b') == ([], [2])


def test_forward_nonstandard_and_cross_record_references_keep_live_checks():
    w = fg_env.load(contract()).world
    w.emit('record', '', data={'record': 'notes', 'entry': 1})
    w.emit('record', '', data={'record': 'notes', 'entry': 1.0})
    assert read(w, 'a') == ([], [])
    post(w, 0, 7)
    assert read(w, 'a') == ([7], [None, None, 7])
    assert read(w, 'b') == ([], [])
    # Record name deliberately differs from the entry's origin. Missing fields
    # must still raise under the full evaluator, not disappear from candidates.
    row = w.post('public', {'value': 8}, None, None, 'probe')
    w.emit('record', '', data={'record': 'notes', 'entry': row['seq']})
    with pytest.raises(RunError, match="no field 'team'"):
        w.events('record', w.entities['a'])


@pytest.mark.parametrize('value', [None, 0, 1, True, 1.0, 'north', [1, 2], {'a': 1}])
def test_scalar_and_unhashable_values_match_expression_equality(value):
    # The low-level world accepts already materialized values; indexing must not
    # assume that a declared integer field can only ever contain Python ints.
    w = fg_env.load(contract()).world
    w.entities['a'].properties['team'] = value
    post(w, value, 1)
    post(w, 'different', 2)
    assert read(w, 'a') == ([1], [1])


def test_numeric_bool_equality_and_entity_id_coercion_match_evaluator():
    w = fg_env.load(contract()).world
    w.entities['a'].properties['team'] = True
    for value in (1, 1.0, True, 0):
        post(w, value, 1)
    assert read(w, 'a') == ([1, 1, 1], [1, 1, 1])
    w.entities['a'].properties['team'] = w.entities['b']
    post(w, 'b', 2)
    assert read(w, 'a') == ([2], [2])


@pytest.mark.parametrize('rule', ['$viewer.team == $it.team and $world.shared',
                                  '$viewer.team == $it.team or $world.shared',
                                  '$viewer.team == $it.author.team'])
def test_complex_rules_retain_general_evaluation(rule):
    assert equality_fields(rule) is None
    w = fg_env.load(contract(rule)).world
    post(w, 0, 1)
    post(w, 1, 2)
    read(w, 'a')
    w.set_world('shared', True)
    read(w, 'a')
    w.set_prop(w.entities['a'], 'team', 1)
    read(w, 'b')


def test_missing_viewer_property_preserves_empty_and_recipient_gated_behavior():
    w = fg_env.load(contract()).world
    del w.entities['b'].properties['team']
    assert read(w, 'b') == ([], [])
    post(w, 0, 1, to=('a',))
    assert read(w, 'b') == ([], [])
    post(w, 0, 2)
    with pytest.raises(RunError, match="no property 'team'"):
        w.visible_records('notes', w.entities['b'])
    with pytest.raises(RunError, match="no property 'team'"):
        w.events('record', w.entities['b'])


def test_shared_reads_charge_matching_work_without_scanning_other_teams():
    from fg_env.sdk.expr import evaluate
    from fg_env.sdk.expr_base import shared_budget

    w = fg_env.load(contract()).world
    for i in range(100):
        post(w, 1, i)
    post(w, 0, 101)
    scope = w.scope(viewer=w.entities['a'])
    with shared_budget(10, 'shared read'):
        assert [r['value'] for r in evaluate('$records(notes)', scope)] == [101]
    with shared_budget(10, 'shared events'):
        assert len(evaluate('$events(record)', scope)) == 1
    for i in range(20):
        post(w, 0, 102 + i)
    with shared_budget(10, 'shared read'):
        with pytest.raises(RunError, match='work budget'):
            evaluate('$records(notes)', scope)


def test_different_field_names_and_string_keys():
    c = contract('$viewer.team == $it.department')
    c['records']['notes']['fields'] = {'department': 'text', 'value': 'int'}
    w = fg_env.load(c).world
    w.entities['a'].properties['team'] = 'sales'
    w.entities['b'].properties['team'] = 'ops'
    for i, department in enumerate(('sales', 'ops', 'sales')):
        w.post('notes', {'department': department, 'value': i}, 'a', None, 'probe')
    assert read(w, 'a') == ([0, 2], [0, 2])
    assert read(w, 'b') == ([1], [1])
