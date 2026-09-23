"""Actions sharing one tool: a flat schema whose `action` lists the legal actions, routed and checked per action."""
import json

import fg_env

SHOP = {
    "name": "Corner shop",
    "clock": {"rounds": 2},
    "types": {"shopper": {"agent": True, "props": {"cash": 10, "apples": 0, "bags": 0}}},
    "entities": {"ann": {"type": "shopper"}},
    "actions": {
        "shop_buy": {"by": "shopper", "tool": "shop", "description": "Buy apples.",
                     "params": {"qty": {"type": "int", "min": 1, "max": 5, "description": "How many apples."}},
                     "when": [{"expr": "$actor.cash > 0", "why": "You have no money."}],
                     "do": ["$actor.cash -= $params.qty", "$actor.apples += $params.qty"]},
        "shop_sell": {"by": "shopper", "tool": "shop", "description": "Sell apples back.",
                      "params": {"qty": {"type": "int", "min": 1, "max": "$actor.apples", "description": "How many apples."}},
                      "when": [{"expr": "$actor.apples > 0", "why": "You have no apples."}],
                      "do": ["$actor.cash += $params.qty", "$actor.apples -= $params.qty"]},
        "shop_bag": {"by": "shopper", "tool": "shop", "description": "Take a bag.",
                     "params": {"colour": {"type": "enum", "values": ["red", "blue"]}},
                     "do": ["$actor.bags += 1"]},
    },
    "stages": [{"name": "shop", "max_actions": 3, "max_calls": 10}],
}


def _turn(contract=SHOP):
    env = fg_env.load(contract, seed=1)
    seen = {}

    def agent(wake):
        seen["tools"] = wake.tools
        seen["calls"] = [wake.call(name, args) for name, args in seen.get("script", [])]
        wake.end()

    return env, seen, agent


def test_shared_actions_become_one_flat_tool_listing_only_the_legal_actions():
    env, seen, agent = _turn()
    env.run(agent, rounds=1)
    tools = {t.name: t for t in seen["tools"]}
    assert "shop" in tools and not {"shop_buy", "shop_sell", "shop_bag"} & set(tools)
    schema = tools["shop"].input_schema
    assert schema["type"] == "object" and schema["required"] == ["action"] and schema["additionalProperties"] is False
    assert not {"oneOf", "anyOf", "allOf"} & set(schema)  # a flat root object, as OpenAI strict mode requires
    assert schema["properties"]["action"]["enum"] == ["buy", "bag"]  # selling needs apples: not offered yet
    assert "qty" in schema["properties"] and "colour" in schema["properties"]
    assert schema["properties"]["colour"]["description"].startswith("Only for bag")
    json.dumps(tools["shop"].to_openai())


def test_a_call_routes_to_the_chosen_action_and_logs_it_under_its_own_name():
    env, seen, agent = _turn()
    seen["script"] = [("shop", {"action": "buy", "qty": 2, "colour": None}), ("shop", {"action": "shop_sell", "qty": 1})]
    result = env.run(agent, rounds=1)
    assert [c.ok for c in seen["calls"]] == [True, True], [c.text for c in seen["calls"]]
    assert env.entity("ann")["props"]["apples"] == 1 and env.entity("ann")["props"]["cash"] == 9
    assert [e["data"]["action"] for e in result.events if e["kind"] == "action"] == ["shop_buy", "shop_sell"]


def test_wrong_calls_to_a_shared_tool_get_a_correction():
    env, seen, agent = _turn()
    seen["script"] = [("shop", {"qty": 2}), ("shop", {"action": "steal"}), ("shop", {"action": "buy", "colour": "red"}),
                      ("shop", {"action": "buy"}), ("shop", {"action": "sell", "qty": 1})]
    result = env.run(agent, rounds=1)
    texts = [c.text for c in seen["calls"]]
    assert not any(c.ok for c in seen["calls"])
    assert "say which `action`" in texts[0] and "buy, bag" in texts[0]
    assert "'steal' is not one of its actions" in texts[1]
    assert "buy does not take colour (it takes: qty)" in texts[2]
    assert "qty" in texts[3] and "Correct the arguments" in texts[3]
    assert "You cannot shop sell now: You have no apples" in texts[4]
    assert all(c.data.get("error") == "invalid" for c in seen["calls"])
    assert result.stats["invalid_calls"] == 5


def test_simultaneous_stages_submit_the_chosen_action():
    contract = {**SHOP, "stages": [{"name": "shop", "turns": "simultaneous", "max_actions": 2}]}
    env, seen, agent = _turn(contract)
    seen["script"] = [("shop", {"action": "buy", "qty": 3})]
    env.run(agent, rounds=1)
    assert seen["calls"][0].ok and "Submitted shop buy" in seen["calls"][0].text
    assert env.entity("ann")["props"]["apples"] == 3
    assert "(Committed when everyone has chosen.)" in {t.name: t for t in seen["tools"]}["shop"].description


def test_random_agents_and_snapshots_work_with_shared_tools():
    straight = fg_env.load(SHOP, seed=4).run().to_dict()
    env = fg_env.load(SHOP, seed=4)
    env.run(rounds=1)
    restored = fg_env.Env.restore(SHOP, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_check_reports_bad_shared_tools():
    clash = json.loads(json.dumps(SHOP))
    clash["actions"]["shop"] = {"by": "shopper", "do": []}
    clash["actions"]["shop_bag"]["params"]["action"] = {"type": "text"}
    clash["actions"]["shop_buy"]["tool"] = "end_turn"
    clash["stages"][0]["actions"] = "all"
    messages = {(i.path, i.message) for i in fg_env.check(clash, rounds=0) if i.severity == "error"}
    assert ("actions.shop_sell.tool", "'shop' is also the name of an action") in messages
    assert ("actions.shop_buy.tool", "'end_turn' is a built-in tool, so a model could never call this one") in messages
    assert any(path == "actions.shop_bag.params.action" for path, _ in messages)
