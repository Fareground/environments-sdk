"""The negotiation recipe enforces private limits as rules for both sides of settlement."""
import json
from pathlib import Path

import pytest

import fg_env

RECIPE = Path(fg_env.__file__).parent / 'authoring/recipes/negotiation.json'


@pytest.mark.parametrize('proposer,price,cost,worth,accepted', [
    ('buyer', 90, 40, 80, False),
    ('seller', 30, 40, 80, False),
    ('buyer', 75, 80, 70, False),
    ('seller', 90, 40, 80, False),
    ('buyer', 30, 40, 80, False),
    ('seller', 60, 40, 80, True),
    ('buyer', 40, 40, 80, True),
    ('seller', 80, 40, 80, True),
])
def test_settlement_checks_both_limits_independent_of_acceptor(proposer, price, cost, worth, accepted):
    calls = []
    offered = False

    def participant(wake):
        nonlocal offered
        if wake.entity_id == proposer and not offered:
            calls.append(('offer', wake.call('offer', {'price': price})))
            offered = True
        elif wake.entity_id != proposer and offered and not any(name == 'accept' for name, _ in calls):
            calls.append(('accept', wake.call('accept', {})))

    result = fg_env.load(json.loads(RECIPE.read_text()), inputs={'cost': cost, 'worth': worth}).run(participant)
    assert result.status in ('completed', 'ended') and result.error is None
    assert any(name == 'accept' for name, _ in calls)
    offer_receipt = next(receipt for name, receipt in calls if name == 'offer')
    assert offer_receipt.ok is (price >= cost if proposer == 'seller' else price <= worth)
    assert next(receipt for name, receipt in calls if name == 'accept').ok is accepted
    if accepted:
        assert result.ok
    assert result.outputs['deal'] is accepted
    assert result.outputs['price'] == (price if accepted else None)


@pytest.mark.parametrize('inputs', [{}, {'cost': 0, 'worth': 0}, {'cost': 80, 'worth': 20}, {'cost': 40, 'worth': 40}])
def test_negotiation_recipe_only_offers_actions_with_feasible_choices(inputs):
    assert fg_env.check(json.loads(RECIPE.read_text()), inputs=inputs) == []


@pytest.mark.parametrize('actor,other_input', [('seller', 'worth'), ('buyer', 'cost')])
def test_initial_tools_do_not_reveal_the_other_partys_private_limit(actor, other_input):
    def tools_for(other_limit):
        observed = []
        def participant(wake):
            if wake.entity_id == actor:
                observed.append(wake.tools)
            wake.end()
        result = fg_env.load(json.loads(RECIPE.read_text()), inputs={other_input: other_limit}, seed=0).run(
            participant, rounds=1)
        assert result.error is None and len(observed) == 1
        return observed
    assert tools_for(20) == tools_for(100)


def test_invariant_prevents_future_alternate_settlement_action_from_bypassing_limits():
    contract = json.loads(RECIPE.read_text())
    contract['actions']['bypass'] = {'by': 'party', 'do': ['$world.price = 999', '$world.deal = true']}
    result = fg_env.load(contract).run(lambda wake: wake.call('bypass', {}))
    assert result.status == 'completed' and result.error is None
    assert result.outputs['deal'] is False
    assert result.outputs['price'] is None
