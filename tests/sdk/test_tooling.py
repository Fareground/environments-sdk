import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.sdk.effects import EFFECT_OPS
from fg_env.sdk.expr import FUNCTIONS
from fg_env.sdk.guide import QUICKSTART, guide, guide_parts, schema
from fg_env.sdk.guide_pages import SECTIONS, function_groups
from fg_env.sdk.registry import FAMILIES

from test_runtime import SHOP


#: The core guide must stay short enough to read before writing a first contract (characters / 4 ≈ tokens).
CORE_TOKENS = 6_000


def test_the_core_guide_is_short_and_maps_every_part():
    core = guide()
    assert len(core) / 4 < CORE_TOKENS
    for name, *_ in SECTIONS:
        assert f"`{name}`" in core
    for family in FAMILIES:
        assert f"| `{family}` |" in core
    for topic in ("model", "expressions", "templates", "effects", "functions", "mechanisms", "patterns", "recipes", "macros",
                  "running", "checklist"):
        assert f"- `{topic}` —" in core
    assert guide("core") == core


def test_every_part_renders_and_all_holds_every_function_effect_section_and_mode():
    whole = guide("all")
    for part in guide_parts()[:-1]:
        assert guide(part).strip(), part
    for name, spec in FUNCTIONS.items():
        assert f"`${spec.signature}`" in whole, name
    for op in EFFECT_OPS:
        assert f"`{op}`" in whole
    for section in fg_env.Contract.model_fields:
        if section not in ("fg_env", "name", "description"):
            assert f"## `{section}`:" in whole or section == "mechanisms", section
    for family in FAMILIES.values():
        for spec in family.modes.values():
            assert guide(spec.key) in whole


def test_every_function_belongs_to_a_named_group():
    groups = function_groups()
    assert "other" not in groups
    listed = [spec.name for specs in groups.values() for spec in specs]
    assert sorted(listed) == sorted(FUNCTIONS)
    assert "$variance(" in guide("functions.stats") and "$auction(" in guide("market")


def test_an_unknown_part_suggests_the_closest_one():
    with pytest.raises(KeyError, match="did you mean 'market.auction'"):
        guide("market.auctoin")
    with pytest.raises(KeyError, match="did you mean 'actions'"):
        guide("action")


def test_cli_guide_prints_a_part_and_suggests_one_for_a_typo(capsys):
    assert main(["guide", "actions"]) == 0
    assert capsys.readouterr().out.startswith("## `actions`:")
    assert main(["guide", "actoins"]) == 1
    assert "did you mean 'actions'" in capsys.readouterr().err


def test_section_pages_list_the_roots_available_there():
    actions = guide("actions")
    assert actions.startswith("## `actions`:") and "| params.*.where | $actor $it $i $params" in actions
    assert "$result" in guide("outputs") and "Roots" not in guide("imports")


def test_the_mechanism_family_table_lists_every_mode():
    table = guide("mechanisms")
    for name, family in FAMILIES.items():
        assert f"| `{name}` |" in table
        page = guide(name)
        for mode, spec in family.modes.items():
            assert f"- `{mode}`:" in page
            mode_page = guide(f"{name}.{mode}")
            assert mode_page.startswith(f"### `{name}.{mode}`")
            for field in spec.config.model_fields:
                assert f"- `{field}` (" in mode_page, (name, mode, field)
            marker = f"Actions of the `{name}` op:"
            listed = mode_page[mode_page.index(marker):] if marker in mode_page else ""
            for action, op in family.actions.get(mode, {}).items():
                assert (f"\n- `{action}`" in listed) is not op.internal, (name, mode, action)


def test_the_quickstart_contract_checks_clean_and_runs_with_defaults():
    quickstart = json.loads(QUICKSTART)
    assert QUICKSTART in guide()
    assert fg_env.check(quickstart) == []
    result = fg_env.run(quickstart, seed=1)
    assert result.ok and result.rounds == 20 and result.outputs["richest"] in ("ann", "bob")


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


def test_cli_reports_user_mistakes_without_tracebacks(tmp_path, capsys):
    from fg_env.__main__ import main

    path = tmp_path / "shop.json"
    path.write_text(json.dumps(SHOP))
    bad_inputs = tmp_path / "inputs.json"
    bad_inputs.write_text("[1, 2]")
    cases = [
        (["check", str(tmp_path / "missing.json")], 1, "file not found"),
        (["run", str(path), "--inputs-file", str(tmp_path / "nope.json")], 1, "cannot read --inputs-file"),
        (["run", str(path), "--inputs-file", str(bad_inputs)], 1, "JSON object"),
        (["run", str(path), "--input", "shoppers"], 1, "name=value"),
        (["run", str(path), "--agent", "shopper=policy:nope"], 1, "unknown participant"),
        (["run", str(path), "--rounds", "-2"], 1, "rounds must be"),
        (["preview", str(path), "shopper_1", "--stage", "nowhere"], 1, "no stage"),
        (["experiment", str(path), "--runs", "0"], 1, "runs must be"),
        (["experiment", str(path), "--arms", "nope"], 1, "not declared"),
    ]
    for argv, status, message in cases:
        assert main(argv) == status, argv
        assert message in capsys.readouterr().err, argv


def test_authoring_guide_example_and_known_answer_run_verbatim(tmp_path, monkeypatch):
    page = guide('authoring')
    assert len(page) < 10_000  # Fits a single reference page, also used by host agents.
    contract_text = page.split('```json\n')[1].split('```')[0]
    scripts = [block.split('```')[0] for block in page.split('```python\n')[1:]]
    (tmp_path / 'scenario.json').write_text(contract_text)
    monkeypatch.chdir(tmp_path)
    for script in scripts:
        exec(compile(script, '<authoring guide>', 'exec'), {})


@pytest.mark.parametrize('rows,capacity,completed,pending', [
    ([], 4, 0, 0),
    ([{'name': 'A', 'quantity': 3}], 0, 0, 3),
    ([{'name': 'B', 'quantity': 2}, {'name': 'A', 'quantity': 3}], 4, 5, 0),
    ([{'name': str(i), 'quantity': 3} for i in range(4)], 4, 8, 4),
])
def test_authoring_example_uses_every_input_row_and_shared_capacity(rows, capacity, completed, pending):
    contract = json.loads(guide('authoring').split('```json\n')[1].split('```')[0])
    env = fg_env.load(contract, inputs={'items': rows, 'facility': {'capacity': capacity}}, seed=1)
    assert len(env.entities('item')) == len(rows)

    def greedy(wake):
        available = capacity
        for entity in env.entities('item'):
            quantity = min(available, entity['props']['pending'], entity['props']['remaining_today'])
            if quantity:
                receipt = wake.call('allocate', {'item': entity['id'], 'quantity': quantity})
                assert receipt.ok, receipt.text
                available -= quantity
        wake.end()

    result = env.run(greedy)
    assert result.ok, result.error
    assert result.outputs == {'completed': completed, 'pending': pending}
    assert completed + pending == sum(row['quantity'] for row in rows)
