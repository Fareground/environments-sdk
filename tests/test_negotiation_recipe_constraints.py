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
    assert result.ok, result.error
    assert any(name == 'accept' for name, _ in calls)
    assert result.outputs['deal'] is accepted
    assert result.outputs['price'] == (price if accepted else None)


def test_invariant_prevents_future_alternate_settlement_action_from_bypassing_limits():
    contract = json.loads(RECIPE.read_text())
    contract['actions']['bypass'] = {'by': 'party', 'do': ['$world.price = 999', '$world.deal = true']}
    result = fg_env.load(contract).run(lambda wake: wake.call('bypass', {}))
    assert result.status == 'completed' and result.error is None
    assert result.outputs['deal'] is False
    assert result.outputs['price'] is None
