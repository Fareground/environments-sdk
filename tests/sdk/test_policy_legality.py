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


def test_a_policy_can_pass_a_list_of_entities_to_a_list_param():
    contract = {"name": "Voters", "clock": {"rounds": 1},
                "types": {"voter": {"agent": True, "policy": "rank", "props": {"ballot": {"type": "list", "default": []}}},
                          "option": {"props": {"appeal": 0}}},
                "entities": {"x": {"type": "option", "props": {"appeal": 1}}, "y": {"type": "option", "props": {"appeal": 2}},
                             "v": {"type": "voter"}},
                "policies": {"rank": {"rules": [{"do": "rank", "with": {"ranking": "$top(option, $it.appeal)"}}]}},
                "actions": {"rank": {"by": "voter", "params": {"ranking": {"type": "list", "of": "option"}},
                                     "do": ["$actor.ballot = $map($params.ranking, $it.id)"], "terminal": True}},
                "stages": [{"name": "vote", "turns": "simultaneous"}]}
    env = fg_env.load(contract, seed=1)
    env.run()
    assert env.entity("v")["props"]["ballot"] == ["y", "x"]
