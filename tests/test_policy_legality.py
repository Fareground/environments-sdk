"""Coded policies decide legality without building tool schemas."""
import fg_env
from fg_env import actions


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


def test_a_rule_whose_action_is_not_legal_is_skipped_before_its_arguments_are_worked_out():
    contract = {"name": "Court", "clock": {"rounds": 3},
                "types": {"clerk": {"agent": True, "policy": "file", "props": {"released": 0}},
                          "exhibit": {"props": {"held": False, "weight": 0}}},
                "entities": {"c": {"type": "clerk"}, "e": {"type": "exhibit", "props": {"weight": 2}}},
                "policies": {"file": {"rules": [
                    # only computable while something is held, which is exactly when `release` is legal
                    {"do": "release", "with": {"exhibit": "$top($filter(exhibit, $it.held), $it.weight, 1)[0]"}},
                    {"do": "hold"}]}},
                "actions": {"release": {"by": "clerk", "when": {"expr": "$any(exhibit, $it.held)", "why": "Nothing held."},
                                        "params": {"exhibit": {"type": "entity", "of": "exhibit"}},
                                        "do": ["$params.exhibit.held = false", "$actor.released += 1"], "terminal": True},
                            "hold": {"by": "clerk", "do": ["$entity(e).held = true"], "terminal": True}}}
    env = fg_env.load(contract, seed=1)
    result = env.run()
    assert result.status == "completed", result.error
    assert env.entity("c")["props"]["released"] == 1  # hold, release, hold
    assert not any(i.severity == "error" for i in fg_env.check(contract))


def test_a_policy_rule_naming_a_parameter_the_action_lacks_is_an_error_with_the_nearest_name():
    c = {"name": "Pot", "clock": {"rounds": 1}, "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
         "actions": {"give": {"by": "p", "params": {"amount": {"type": "int", "min": 0, "max": 5}}, "do": []}},
         "policies": {"stingy": {"rules": [{"do": "give", "with": {"amt": 0}}]}}}
    found = [i for i in fg_env.check(c) if i.path == "policies.stingy.rules[0].with.amt"]
    assert found and found[0].severity == "error" and "'give' has no parameter 'amt'" in found[0].message
    assert "amount" in found[0].fix
