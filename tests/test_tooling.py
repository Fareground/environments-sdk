from pathlib import Path
import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.effects import EFFECT_OPS
from fg_env.expr import FUNCTIONS
from fg_env.guides import guide, guide_parts, schema
from fg_env.guides.pages import SECTIONS, function_groups
from fg_env.registry import FAMILIES

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


def test_the_authoring_page_is_the_core_guide_start_and_never_sends_an_author_to_everything():
    page, core = guide("authoring"), guide()
    start = page[:page.index("## Read next")]
    assert core.startswith(start)
    assert page.index("Faithful first, configurable second") < page.index("## Worked example")
    for text in (page, core):
        assert "guide('all')" not in text and "guide all" not in text
    assert guide("all").count("## Worked example") == 1  # the start page appears once, inside the core guide


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


def test_the_function_map_keeps_core_functions_apart_from_mechanism_ones():
    core, _, families = guide("functions").partition("Mechanism functions")
    assert "$sum" in core and "$lookup" in core
    assert "$poker_hand" not in core and "$wordle_feedback" not in core and "$board_moves" not in core
    assert "$poker_hand" in families and "$wordle_feedback" in guide("functions.game")


def test_an_unknown_part_suggests_the_closest_one():
    for group in ("math", "collections", "stats"):
        with pytest.raises(KeyError, match=f"did you mean 'functions.{group}'"):
            guide(group)
        assert guide(f"functions.{group}").strip()
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
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(looping, seed=1)
    result = failed.value.result
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
    assert len(page) < 10_000  # One page an authoring agent starts from.
    contract_text = page.split('```json\n')[1].split('```')[0]
    scripts = [block.split('```')[0] for block in page.split('```python\n')[1:]]
    money_page = (Path(__file__).resolve().parents[1] / 'docs/sdk/authoring.md').read_text()
    money_section = money_page.split('## Exact monetary budgets\n')[1].split('\n## ')[0]
    scripts += [block.split('```')[0] for block in money_section.split('```python\n')[1:]]
    (tmp_path / 'lake.json').write_text(contract_text)
    monkeypatch.chdir(tmp_path)
    for script in scripts:
        exec(compile(script, '<authoring guide>', 'exec'), {})


def test_check_configured_inputs_without_mutating_defaults(tmp_path, capsys):
    contract = {"name": "Configured check", "types": {}, "world": {"value": 0},
                "inputs": {"settings": {"type": "map", "default": {}, "fields": {
                    "scale": {"type": "number", "default": 2, "min": 0}}}},
                "events": [{"do": "$world.value = 10 / $inputs.settings.scale"}]}
    original = json.loads(json.dumps(contract))
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    supplied = {"settings": {"scale": 0}}
    assert not [i for i in fg_env.check(contract, rounds=0, inputs=supplied) if i.severity == "error"]
    assert any("division by zero" in i.message for i in fg_env.check(contract, inputs=supplied))
    invalid = fg_env.check(contract, rounds=0, inputs={"settings": {"scale": -1}})
    assert any(i.path == "inputs.settings" and i.severity == "error" for i in invalid)
    assert contract == original and supplied == {"settings": {"scale": 0}}
    path = tmp_path / "configured.json"
    path.write_text(json.dumps(contract))
    values = tmp_path / "inputs.json"
    values.write_text(json.dumps(supplied))
    for flags in (["--input", 'settings={"scale":0}'], ["--inputs-file", str(values)]):
        assert main(["check", str(path), "--json", *flags]) == 1
        assert any("division by zero" in i["message"] for i in json.loads(capsys.readouterr().out))
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize('fishers,seasons,caught,fish_left', [(3, 5, 132, 0), (1, 2, 20, 100), (5, 1, 50, 60)])
def test_the_worked_example_is_driven_by_its_inputs(fishers, seasons, caught, fish_left):
    contract = json.loads(guide('authoring').split('```json\n')[1].split('```')[0])
    env = fg_env.load(contract, inputs={'fishers': fishers, 'seasons': seasons}, seed=1)
    assert len(env.entities('fisher')) == fishers

    def greedy(wake):
        assert wake.call('catch', {'amount': 10}).ok
        wake.end()

    result = env.run(greedy)
    assert result.ok and result.rounds == seasons, result.error
    assert sum(result.outputs['catch_by_fisher'].values()) == caught and result.outputs['fish_left'] == fish_left


def test_ordered_processing_reference_runs_without_priority_scaling():
    page = guide('effects').split('### Ordered processing')[1]
    effect = json.loads(page.split('```json\n')[1].split('```')[0])
    contract = {"name": "Ordered work", "clock": {"rounds": 1},
                "types": {"item": {"props": {"due": 0, "sequence": 0}}},
                "entities": {"later": {"type": "item", "props": {"due": 2, "sequence": 0}},
                             "early_second": {"type": "item", "props": {"due": 1, "sequence": 2000001}},
                             "early_first": {"type": "item", "props": {"due": 1, "sequence": 1}}},
                "world": {"seen": []}, "events": [{"do": [effect]}],
                "outputs": {"seen": "$world.seen"}}
    result = fg_env.run(contract)
    assert result.ok
    assert result.outputs['seen'] == ['early_first', 'early_second', 'later']
