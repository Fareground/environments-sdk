"""A small store for tests of the demand and replenishment modes."""
import copy

LEDGER = {"kind": "economy", "mode": "ledger", "who": "store", "currencies": {"cash": {"start": 100}}}


def store(rounds=20, skus=None, **shop):
    """Two SKUs of one part (a: 8 a week at 10, b: 4 a week at 8, 30 of each in stock), a store entity, a few patterns
    to read, and a demand mechanism ``shop`` whose config ``shop`` overrides."""
    config = {"kind": "economy", "mode": "demand", "items": "sku", "stock": "stock", "price": "$it.list",
              "cost": "$it.list * 0.5", "group": "$it.part", "rate": "$it.base"}
    config.update(shop)
    rows = skus or [{"sku": "a", "part": "pads", "base": 8.0, "list": 10.0, "sibling": "b", "stock": 30},
                    {"sku": "b", "part": "pads", "base": 4.0, "list": 8.0, "sibling": "a", "stock": 30}]
    return {
        "name": "Store",
        "clock": {"rounds": rounds, "unit": "week", "start": "2026-01-05"},
        "inputs": {"skus": {"type": "table", "default": copy.deepcopy(rows)}},
        "types": {"sku": {"props": {"part": "", "base": 0.0, "list": 0.0, "sibling": ""}},
                  "store": {"props": {}}, "buyer": {"agent": True}},
        "entities": {"store": {"type": "store"}},
        "population": [{"type": "sku", "from": "$inputs.skus", "id": "{$row.sku}",
                        "props": {"part": "$row.part", "base": "$row.base", "list": "$row.list", "sibling": "$row.sibling",
                                  "stock": "$row.stock"}}],
        "patterns": {"sales": {"kind": "counts", "dispersion": 4},
                     "price_effect": {"kind": "elasticity", "elasticity": -1.3, "reference": 1.0}},
        "mechanisms": {"shop": config},
    }


def with_ledger(contract):
    """The contract with a ledger declared first, holding the store's cash."""
    out = copy.deepcopy(contract)
    out["mechanisms"] = {"money": copy.deepcopy(LEDGER), **out["mechanisms"]}
    return out


def item(env, item_id):
    return env.world.entities[item_id].properties
