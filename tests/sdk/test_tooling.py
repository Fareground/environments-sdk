import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.sdk.effects import EFFECT_OPS
from fg_env.sdk.expr import FUNCTIONS
from fg_env.sdk.guide import guide, schema

from test_runtime import SHOP


def test_guide_covers_every_function_effect_and_section():
    text = guide()
    for name in FUNCTIONS:
        assert f"${name}(" in text
    for op in EFFECT_OPS:
        assert f"`{op}`" in text
    for section in fg_env.Contract.model_fields:
        if section not in ("fg_env", "name", "description"):
            assert f"### {section}:" in text
    assert guide("effects").startswith("## Effects")
    with pytest.raises(KeyError):
        guide("nope")


def test_guide_example_contract_is_valid_and_runs():
    text = guide("overview")
    start = text.index("```json") + len("```json")
    example = json.loads(text[start:text.index("```", start)])
    assert [i for i in fg_env.check(example) if i.severity == "error"] == []
    result = fg_env.run(example, seed=1)
    assert result.ok, result.summary()
    assert result.outputs["winner"] in ("Ana", "Ben")


def test_schema_describes_the_contract():
    data = schema()
    assert "actions" in data["properties"] and "types" in data["required"]


def test_cli_check_run_preview(tmp_path, capsys):
    path = tmp_path / "shop.json"
    path.write_text(json.dumps(SHOP))
    assert main(["check", str(path)]) == 0
    assert main(["run", str(path), "--seed", "2", "--agent", "shopper=policy:thrifty", "--json"]) == 0
    out = capsys.readouterr().out
    result = json.loads(out[out.index("{"):])
    assert result["outputs"]["units_sold"] > 0
    assert main(["preview", str(path), "shopper_1"]) == 0
    assert "=== tools ===" in capsys.readouterr().out
    broken = dict(SHOP, stages=[{"name": "shop", "actions": ["buyy"]}])
    path.write_text(json.dumps(broken))
    assert main(["check", str(path)]) == 1


REPEAT = {
    "name": "Order matching",
    "clock": {"rounds": 1},
    "world": {"trades": 0},
    "types": {"trader": {"agent": True, "props": {}},
              "order": {"props": {"side": {"type": "enum", "values": ["buy", "sell"], "default": "buy"},
                                  "price": 0, "qty": 1}}},
    "entities": {
        "t": {"type": "trader"},
        "b1": {"type": "order", "props": {"side": "buy", "price": 11}},
        "b2": {"type": "order", "props": {"side": "buy", "price": 9}},
        "s1": {"type": "order", "props": {"side": "sell", "price": 10}},
        "s2": {"type": "order", "props": {"side": "sell", "price": 12}},
    },
    "actions": {"wait": {"by": "trader", "do": []}},
    "events": [{"phase": "end", "do": [{
        "repeat": 10,
        "while": "$count(order, $it.side == buy) > 0 and $count(order, $it.side == sell) > 0 and "
                 "$top(order, $it.price, 1, $it.side == buy)[0].price >= $bottom(order, $it.price, 1, $it.side == sell)[0].price",
        "do": ["$bid = $top(order, $it.price, 1, $it.side == buy)[0]",
               "$ask = $bottom(order, $it.price, 1, $it.side == sell)[0]",
               {"remove": "$bid"}, {"remove": "$ask"}, "$world.trades += 1"]}]}],
    "outputs": {"trades": {"expr": "$world.trades", "type": "int"},
                "resting": {"expr": "$count(order)", "type": "int"}},
}


def test_repeat_matches_until_the_book_is_uncrossed():
    result = fg_env.run(REPEAT, seed=1)
    assert result.ok, result.summary()
    assert result.outputs == {"trades": 1, "resting": 2}


def test_repeat_limit_is_an_error_not_a_silent_stop():
    looping = json.loads(json.dumps(REPEAT))
    looping["events"][0]["do"][0]["do"] = ["$world.trades += 1"]
    result = fg_env.run(looping, seed=1)
    assert result.status == "failed"
    assert "reached its limit of 10" in result.error
