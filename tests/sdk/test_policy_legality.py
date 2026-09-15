"""Coded policies decide legality without building tool schemas."""
import fg_env
from fg_env.sdk import actions


def test_coded_policies_never_build_tool_schemas(monkeypatch):
    built = {"n": 0}
    original = actions.ActionBook.tool

    def counting(self, *args, **kwargs):
        built["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(actions.ActionBook, "tool", counting)
    contract = {"name": "Sellers", "clock": {"rounds": 4},
                "types": {"seller": {"agent": True, "policy": "sell", "props": {"stock": 3}}},
                "population": [{"type": "seller", "count": 5}],
                "policies": {"sell": {"rules": [{"when": "$actor.stock > 0", "do": "sell_one"}]}},
                "actions": {"sell_one": {"by": "seller", "when": {"expr": "$actor.stock > 0", "why": "Sold out."},
                                         "do": ["$actor.stock -= 1"], "terminal": True}},
                "stages": [{"name": "market", "turns": "sequential"}]}
    env = fg_env.load(contract, seed=1)
    result = env.run()
    assert result.status == "completed", result.error
    assert all(e["props"]["stock"] == 0 for e in env.entities("seller"))
    assert built["n"] == 0
