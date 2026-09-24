"""Earlier state, outcome and reuse sections rewritten into their current homes: each rule JSON in, JSON out;
rewriting twice changes nothing; a document mixing both forms works; what cannot be rewritten is refused with how
to say it now."""
import pytest

import fg_env
from fg_env.contract.normalize import normalize
from fg_env.errors import ContractError

BASE = {"name": "x", "types": {"buyer": {"agent": True, "props": {"cash": 10}}, "shop": {"props": {"stock": 0}}},
        "actions": {"buy": {"by": "buyer", "do": []}}}


def rewritten(**sections):
    out, notes = normalize({**BASE, **sections})
    assert normalize(out) == (out, [])  # idempotent
    return out, notes


def test_metrics_become_series_outputs_and_their_reads_become_outputs():
    out, notes = rewritten(metrics={"cash": "$sum(buyer, $it.cash)", "rich": {"expr": "$outputs.cash > 5",
                                                                              "unit": "flag"}},
                           outputs={"final": "$metrics.cash * 2"}, views={"v": {"show": "{$metrics.cash}"}})
    assert out["outputs"] == {"final": "$outputs.cash * 2", "cash": {"expr": "$sum(buyer, $it.cash)", "series": True},
                              "rich": {"expr": "$outputs.cash > 5", "unit": "flag", "series": True}}
    assert out["views"]["v"]["show"] == "{$outputs.cash}" and "metrics" not in out and notes


def test_a_metric_named_like_an_output_becomes_that_outputs_series():
    out, _ = rewritten(metrics={"sold": "$world.today", "gini": "$world.g"},
                       outputs={"sold": "$world.total", "gini": {"expr": "$metrics.gini", "type": "number"}})
    assert out["outputs"]["sold"] == {"expr": "$world.total", "series": "$world.today"}
    assert out["outputs"]["gini"] == {"expr": "$world.g", "type": "number", "series": True}


def test_a_series_output_keeps_the_old_run_result_shape():
    contract = {**BASE, "entities": {"ann": {"type": "buyer"}}, "clock": {"rounds": 3},
                "world": {"n": 0}, "events": [{"phase": "end", "do": "$world.n += 1"}],
                "metrics": {"n": "$world.n"}, "outputs": {"total": "$sum($series.n, $it)"}}
    result = fg_env.run(contract, "idle", seed=1)
    assert result.metrics == {"n": 3} and result.series == {"n": [1, 2, 3]}
    assert result.outputs == {"total": 6, "n": 3}


def test_a_top_level_policy_moves_under_each_type_that_plays_it():
    out, _ = rewritten(types={**BASE["types"], "buyer": {**BASE["types"]["buyer"], "policy": "thrifty"}},
                       policies={"thrifty": {"rules": [{"do": "buy"}]}, "idle": {"rules": [{"do": "pass"}]}})
    assert out["types"]["buyer"]["policies"] == {"thrifty": {"rules": [{"do": "buy"}]},
                                                 "idle": {"rules": [{"do": "pass"}]}}
    assert "policies" not in out and "policies" not in out["types"]["shop"]


def test_population_becomes_generators_after_the_named_entities_keyed_by_type():
    out, _ = rewritten(entities={"shop": {"type": "shop"}},
                       population=[{"type": "shop", "count": 2}, {"type": "buyer", "count": 3}])
    assert list(out["entities"]) == ["shop", "shop_2", "buyer"]
    assert out["entities"]["buyer"] == {"type": "buyer", "count": 3}


@pytest.mark.parametrize("extra, fix", [
    ({"mix": [{"name": "a"}]}, "assign_labels"), ({"members": []}, "second generator"),
    ({"raking": {"margins": {}}}, "personas.rake"), ({"quota": True}, "assign_labels"),
])
def test_the_population_extras_are_refused_with_how_to_say_them_now(extra, fix):
    with pytest.raises(ContractError) as info:
        normalize({**BASE, "population": [{"type": "buyer", "count": 2, **extra}]})
    [issue] = info.value.issues
    assert issue.path == f"population[0].{next(iter(extra))}" and fix in issue.fix


def test_links_move_under_their_relation_and_an_unknown_relation_is_named():
    out, _ = rewritten(relations={"knows": {"symmetric": True}},
                       links=[{"relation": "knows", "among": "buyer", "graph": "ring"}])
    assert out["relations"]["knows"]["links"] == [{"among": "buyer", "graph": "ring"}] and "links" not in out
    with pytest.raises(ContractError, match="'knws' is not a declared relation"):
        normalize({**BASE, "relations": {"knows": {}}, "links": [{"relation": "knws", "among": "buyer"}]})


def test_the_game_section_becomes_the_score_of_its_players():
    out, _ = rewritten(game={"players": "buyer", "returns": "$actor.cash - 10", "seat": "$it.id",
                             "utility": "zero_sum", "min_return": -10, "max_return": 10, "dynamics": "sequential"})
    assert out["types"]["buyer"]["score"] == {"value": "$it.cash - 10", "seat": "$it.id", "utility": "zero_sum",
                                              "min": -10, "max": 10}
    assert "game" not in out
    with pytest.raises(ContractError, match="rewards"):
        normalize({**BASE, "game": {"returns": "1", "rewards": "1"}})


def test_blocks_become_effect_defs_run_with_call():
    out, _ = rewritten(blocks={"pay": {"args": ["who"], "do": ["$who.cash -= 1"]}},
                       actions={"buy": {"by": "buyer", "do": [{"block": "pay", "with": {"who": "$actor"}}]}})
    assert out["defs"] == {"pay": {"args": ["who"], "do": ["$who.cash -= 1"]}}
    assert out["actions"]["buy"]["do"] == [{"call": "pay", "with": {"who": "$actor"}}]
    contract = {**out, "entities": {"ann": {"type": "buyer"}}, "clock": {"rounds": 1}}
    assert fg_env.run(contract, lambda wake: wake.call("buy"), seed=1).status == "completed"


def test_assets_become_file_inputs():
    out, _ = rewritten(assets={"deed": {"file": "docs/deed.pdf", "caption": "The deed", "type": "pdf"},
                               "photos": {"folder": "docs/photos", "tags": ["x"]}})
    assert out["inputs"] == {"deed": {"type": "file", "source": "docs/deed.pdf", "caption": "The deed"},
                             "photos": {"type": "file", "source": "docs/photos", "tags": ["x"]}}


def test_an_arm_patch_in_an_earlier_form_is_rewritten_too():
    out, _ = rewritten(arms={"rich": {"patch": {"metrics": {"cash": "$sum(buyer, $it.cash)"}}}})
    assert out["arms"]["rich"]["patch"] == {"outputs": {"cash": {"expr": "$sum(buyer, $it.cash)", "series": True}}}


def test_a_document_mixing_both_forms_keeps_the_current_parts():
    out, _ = rewritten(outputs={"cash": {"expr": "$sum(buyer, $it.cash)", "series": True}},
                       metrics={"stock": "$sum(shop, $it.stock)"},
                       entities={"buyer": {"type": "buyer", "count": 2}}, population=[{"type": "buyer", "count": 1}])
    assert set(out["outputs"]) == {"cash", "stock"} and list(out["entities"]) == ["buyer", "buyer_2"]
