"""Business cases compose player decisions, noisy demand, inventory, delays and measurement."""
import json
from pathlib import Path

import pytest

import fg_env

CASES = Path(__file__).parents[1] / 'examples' / 'contracts' / 'business_acceptance'
PATHS = sorted(CASES.glob('*.json'))
assert len(PATHS) == 3, 'all three business acceptance contracts must be present'


@pytest.mark.parametrize('path', PATHS, ids=[p.stem for p in PATHS])
def test_business_accounting_and_counterfactual_demand(path):
    contract = json.loads(path.read_text())
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == 'error']
    initial = sum(e['props']['initial'] * e['props']['cost'] for e in contract['entities'].values()
                  if e['type'] == 'product')
    outputs = {}
    for arm in ('baseline', 'thin_stock', 'focal_discount'):
        result = fg_env.run(contract, seed=19, arm=arm)
        assert result.status == 'completed', result.error
        assert result.stats['invalid_calls'] == result.stats['rejected_actions'] == 0
        out = outputs[arm] = result.outputs
        assert out['requests'] == out['sales'] + out['unmet']
        assert out['operating_profit'] == pytest.approx(
            out['cash_change'] - out['pending_refunds'] + out['inventory_value'] - initial)
    baseline = outputs['baseline']['requests_by_product']
    assert outputs['thin_stock']['requests_by_product'] == baseline
    for sku, requests in baseline.items():
        changed = outputs['focal_discount']['requests_by_product'][sku]
        assert changed >= requests if sku.startswith('atlas_') else changed <= requests
    base = outputs['baseline']
    if path.stem == 'stadium_food_market':
        assert base['waste'] > 0 and base['stock'] == 0
    elif path.stem == 'refurbished_electronics':
        assert base['returns'] > 0 and base['substitutions'] > 0 and base['pending_refunds'] > 0
    else:
        assert base['bundles_sold'] > 0


@pytest.mark.parametrize('path', PATHS, ids=[p.stem for p in PATHS])
def test_business_replay_with_deliveries_and_observation_preview(path):
    env = fg_env.load(path, seed=19)
    env.run(rounds=8)
    snapshot = json.loads(json.dumps(env.snapshot()))
    preview = env.preview('atlas')
    assert 'Operating position' in json.dumps(preview)
    assert env.snapshot() == snapshot
    resumed = fg_env.Env.restore(path, snapshot).run()
    assert resumed.to_dict() == fg_env.run(path, seed=19).to_dict()
