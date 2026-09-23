"""Repeated business what-ifs retain current rules while selecting new arms explicitly."""
import copy
import json

import pytest

import fg_env
from fg_env import ContractError


def rules(multiplier):
    return [{'phase': 'end', 'do': [f'$world.total += {multiplier} * $inputs.demand']}]


def contract():
    return {'name': 'Demand what-ifs', 'clock': {'rounds': 6},
            'inputs': {'demand': {'type': 'int', 'default': 1}},
            'world': {'total': 0}, 'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
            'events': rules(1), 'metrics': {'total': '$world.total'},
            'outputs': {'total': '$world.total'},
            'arms': {'red': {'inputs': {'demand': 2}, 'patch': {'events': rules(5)}},
                     'blue': {'inputs': {'demand': 3}, 'patch': {'events': rules(7)}}}}


@pytest.mark.parametrize('arm', [None, 'red'])
@pytest.mark.parametrize('change', ['inputs', 'seed', 'effects', 'patch', 'same_arm'])
def test_followup_changes_preserve_previous_rule_patch(arm, change):
    original = fg_env.load(contract(), seed=12, arm=arm)
    original.run(rounds=1)
    first = original.fork(patch={'events': rules(2)})
    first.run(rounds=1)
    start = first.props['total']
    opts = {'inputs': {'inputs': {'demand': 4}}, 'seed': {'seed': 42},
            'effects': {'effects': ['$world.total += 10']},
            'patch': {'patch': {'outputs': {'extra': '$world.total + 1'}}},
            'same_arm': {'arm': arm}}[change]
    second = first.fork(**opts)
    result = second.run()
    expected = start + (10 if change == 'effects' else 0) + 4 * 2 * (4 if change == 'inputs' else first.inputs['demand'])
    assert result.ok
    assert result.outputs['total'] == expected
    if change == 'patch':
        assert result.outputs['extra'] == expected + 1
    assert first.props['total'] == start
    assert original.props['total'] == (1 if arm is None else 10)


def test_three_patches_accumulate_and_restored_continuation_matches():
    original = fg_env.load(contract(), seed=12)
    original.run(rounds=2)
    first = original.fork(patch={'events': rules(2)})
    first.run(rounds=1)
    second = first.fork(inputs={'demand': 3})
    second.run(rounds=1)
    third = second.fork(patch={'outputs': {'adjusted': '$world.total + 11'}}, effects=['$world.total += 5'])
    restored = fg_env.Env.restore(third.contract, json.loads(json.dumps(third.snapshot())))
    assert third.run().to_dict() == restored.run().to_dict()
    assert third.result().outputs == {'total': 27, 'adjusted': 38}
    assert third.result().series['total'] == [1, 2, 4, 10, 21, 27]
    assert original.props['total'] == 2
    assert first.props['total'] == 4
    assert second.props['total'] == 10


@pytest.mark.parametrize('arm', [None, 'red'])
def test_snapshot_api_retains_the_effective_contract_without_reapplying_its_arm(arm):
    env = fg_env.load(contract(), seed=12, arm=arm).fork(patch={'events': rules(2)})
    env.run(rounds=2)
    snapshot = json.loads(json.dumps(env.snapshot()))
    branch = fg_env.fork(env.contract, snapshot, inputs={'demand': 4})
    restored = fg_env.Env.restore(env.contract, snapshot).fork(inputs={'demand': 4})
    live = env.fork(inputs={'demand': 4})
    assert branch.run().to_dict() == restored.run().to_dict() == live.run().to_dict()
    assert live.result().outputs['total'] == env.props['total'] + 32


@pytest.mark.parametrize('arm, demand, rate', [('blue', 3, 7), (None, 1, 1)])
def test_selecting_a_different_arm_uses_its_declared_rule_base(arm, demand, rate):
    env = fg_env.load(contract(), seed=12, arm='red')
    env.run(rounds=1)
    first = env.fork(patch={'events': rules(2)})
    first.run(rounds=1)
    changed = first.fork(arm=arm)
    assert changed.inputs['demand'] == demand
    assert changed.run().outputs['total'] == first.props['total'] + 4 * rate * demand
    # An unchanged arm, in contrast, is a continuation of the current rules.
    assert first.fork(arm='red').run().outputs['total'] == first.props['total'] + 16


def test_replacement_contract_selects_new_rules_then_accepts_further_patches():
    first = fg_env.load(contract(), seed=12).fork(patch={'events': rules(2)})
    first.run(rounds=1)
    replacement = contract()
    replacement['events'] = rules(3)
    second = first.fork(contract=replacement)
    third = second.fork(patch={'outputs': {'adjusted': '$world.total + 10'}})
    assert third.run().outputs == {'total': 17, 'adjusted': 27}


def test_repeated_fork_still_checks_current_state_against_removed_fields():
    first = fg_env.load(contract()).fork(patch={'world': {'bonus': 5}})
    replacement = copy.deepcopy(contract())
    with pytest.raises(ContractError, match='bonus'):
        first.fork(contract=replacement)
    assert first.props['bonus'] == 5


def test_clone_of_patched_branch_keeps_its_live_contract_for_the_next_fork():
    first = fg_env.load(contract(), seed=12, arm='red').fork(patch={'events': rules(2)})
    first.run(rounds=2)
    second = first.clone().fork(inputs={'demand': 4})
    assert second.run().outputs['total'] == 40
