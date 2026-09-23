"""Saved variant runs preserve the rule base needed for future what-if selection."""
import copy
import json

import pytest

import fg_env
from fg_env import SnapshotError
from fg_env.api import contract_source
from fg_env.snapshot import contract_hash


def rules(rate):
    return [{'phase': 'end', 'do': [f'$world.total += {rate} * $inputs.demand']}]


def contract():
    return {'name': 'Saved demand alternatives', 'clock': {'rounds': 3},
            'inputs': {'demand': {'type': 'int', 'default': 1}},
            'world': {'total': 0}, 'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
            'events': rules(1), 'outputs': {'total': '$world.total'},
            'arms': {'red': {'inputs': {'demand': 2}, 'patch': {'events': rules(5)}},
                     'blue': {'inputs': {'demand': 3}, 'patch': {'events': rules(7)}}}}


@pytest.mark.parametrize('patched', [False, True])
@pytest.mark.parametrize('target', [None, 'red', 'blue'])
def test_restored_and_snapshot_forks_select_the_same_future_rules_as_live(patched, target):
    live = fg_env.load(contract(), arm='red', seed=14)
    live.run(rounds=1)
    if patched:
        live = live.fork(patch={'events': rules(2)})
    snap = json.loads(json.dumps(live.snapshot()))
    restored = fg_env.Env.restore(live.contract, snap)
    via_restore = restored.fork(arm=target).run()
    direct = fg_env.fork(live.contract, snap, arm=target).run()
    expected = 10 + 2 * ({None: 1, 'red': 4 if patched else 10, 'blue': 21}[target])
    assert via_restore.ok and via_restore.outputs['total'] == expected
    assert via_restore.to_dict() == direct.to_dict() == live.fork(arm=target).run().to_dict()


def test_multiple_json_roundtrips_and_clones_keep_the_original_rule_base():
    env = fg_env.load(contract(), arm='red', seed=14).fork(patch={'events': rules(2)})
    env.run(rounds=1)
    for _ in range(3):
        env = fg_env.Env.restore(env.contract, json.loads(json.dumps(env.snapshot()))).clone()
    assert env.fork(arm=None).run().outputs['total'] == 6
    assert env.fork(arm='blue').run().outputs['total'] == 46


def test_unchanged_default_contract_needs_no_duplicate_source():
    env = fg_env.load(contract())
    assert 'rule_origin' not in env.snapshot()
    env = env.fork(inputs={'demand': 2})
    assert 'rule_origin' not in env.snapshot()


def test_snapshot_source_is_detached_from_live_contract():
    env = fg_env.load(contract(), arm='red')
    snap = env.snapshot()
    snap['rule_origin']['source']['events'] = rules(100)
    assert env.fork(arm=None).run().outputs['total'] == 3
    with pytest.raises(SnapshotError, match='rule_origin.*changed'):
        fg_env.Env.restore(env.contract, snap)


@pytest.mark.parametrize('payload', [None, [], {}, {'hash': 'x'}, {'source': {}, 'hash': 1},
                                    {'source': {'name': 'invalid'}, 'hash': 'x'}])
def test_malformed_provenance_has_an_actionable_snapshot_error(payload):
    env = fg_env.load(contract(), arm='red')
    snap = env.snapshot()
    snap['rule_origin'] = payload
    with pytest.raises(SnapshotError, match='rule_origin'):
        fg_env.Env.restore(env.contract, snap)


def test_provenance_never_bypasses_current_contract_matching():
    env = fg_env.load(contract(), arm='red')
    altered = contract()
    altered['events'] = rules(10)
    altered['arms'] = {}
    with pytest.raises(SnapshotError):
        fg_env.Env.restore(altered, env.snapshot())


def test_legacy_snapshot_continuation_and_original_source_variant_selection():
    source = contract()
    env = fg_env.load(source, arm='red', seed=14)
    env.run(rounds=1)
    legacy = env.snapshot()
    del legacy['rule_origin']
    assert fg_env.Env.restore(env.contract, legacy).run().to_dict() == env.run().to_dict()
    assert fg_env.Env.restore(source, legacy).fork(arm=None).run().outputs['total'] == 12


def test_original_source_contract_and_effective_contract_restore_agree():
    source = contract()
    env = fg_env.load(source, arm='red', seed=14)
    env.run(rounds=1)
    snap = env.snapshot()
    original = fg_env.Env.restore(source, snap)
    effective = fg_env.Env.restore(env.contract, snap)
    assert original.fork(arm=None).run().to_dict() == effective.fork(arm=None).run().to_dict()


def test_contract_folder_is_carried_from_the_supplied_effective_contract(tmp_path):
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(contract()))
    env = fg_env.load(path, arm='red')
    restored = fg_env.Env.restore(env.contract, env.snapshot())
    assert restored.origin.unarmed._folder == env.origin.unarmed._folder == str(tmp_path)


def test_imports_are_resolved_in_saved_source_without_reopening_files(tmp_path):
    source = contract()
    fragment = tmp_path / 'fragment.json'
    fragment.write_text(json.dumps({'events': source.pop('events')}))
    source['imports'] = ['fragment.json']
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(source))
    env = fg_env.load(path, arm='red')
    snap = json.loads(json.dumps(env.snapshot()))
    fragment.unlink()
    assert fg_env.Env.restore(env.contract, snap).fork(arm=None).run().outputs['total'] == 3


def test_replacement_contract_establishes_its_own_origin():
    first = fg_env.load(contract(), arm='red').fork(patch={'events': rules(2)})
    replacement = copy.deepcopy(contract())
    replacement['events'] = rules(11)
    changed = first.fork(contract=replacement)
    restored = fg_env.Env.restore(changed.contract, changed.snapshot())
    assert restored.fork(arm=None).run().outputs['total'] == 33
    assert contract_hash(restored.origin.unarmed) == contract_hash(fg_env.parse(replacement))
    assert contract_source(restored.origin.unarmed)['events'] == rules(11)


@pytest.mark.parametrize('patched', [False, True])
def test_recorded_branch_replays_with_its_effective_contract(patched):
    root = fg_env.load(contract(), arm='red', seed=14, exposures=True)
    root.run(rounds=1)
    branch = root.fork(patch={'events': rules(2)}) if patched else root.fork(inputs={'demand': 4})
    result = branch.run()
    replayed = fg_env.analysis.trace(result).replay(branch.contract)
    assert replayed.ok, replayed.message
    assert replayed.result.outputs == result.outputs


def test_replay_against_intentionally_changed_rules_still_reports_divergence():
    root = fg_env.load(contract(), arm='red', seed=14, exposures=True)
    root.run(rounds=1)
    branch = root.fork(patch={'events': rules(2)})
    result = branch.run()
    changed = contract_source(branch.contract)
    changed['events'] = rules(100)
    changed['arms']['red']['patch']['events'] = rules(100)
    replayed = fg_env.analysis.trace(result).replay(changed)
    assert not replayed.ok
    assert 'outputs.total' in replayed.message


def test_serialized_rule_origin_does_not_expand_saved_import_paths(tmp_path):
    env = fg_env.load(contract(), arm='red')
    snap = env.snapshot()
    source = snap['rule_origin']['source']
    source['imports'] = [str(tmp_path / 'does-not-exist.json')]
    # The parser checks saved structure directly instead of treating imports as
    # new file-reading instructions; checksum rejection is the expected error.
    with pytest.raises(SnapshotError, match='rule_origin.*changed'):
        fg_env.Env.restore(env.contract, snap)
