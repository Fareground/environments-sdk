"""Capabilities added after the LLM side-agent stress test, each with the finding it answers."""
import copy
import json

import pytest

import fg_env
from fg_env import ContractError
from fg_env.__main__ import main

EXCHANGE = {
    "name": "Tiny exchange",
    "clock": {"rounds": 2},
    "world": {"trades": 0, "last": 10.0},
    "types": {
        "trader": {"agent": True, "props": {"cash": 100.0, "shares": 5, "locked": 0.0,
                                            "style": {"type": "enum", "values": ["maker", "taker", "none"], "default": "none"}},
                   "inspect": "$viewer.id == $it.id"},
        "maker": {"extends": "trader"},
        "taker": {"extends": "trader", "props": {"urgency": 0.5}},
        "order": {"props": {"owner": "", "price": 0.0, "qty": 1, "seq": 0}, "inspect": False},
    },
    "entities": {"m": {"type": "maker", "name": "Mo"}, "t": {"type": "taker", "name": "Tia"}},
    "defs": {"notional": {"args": ["price", "qty"], "expr": "$price * $qty"},
             "reserved": {"args": ["who"], "expr": "$sum(order, $notional($it.price, $it.qty), $it.owner == $who.id)"}},
    "blocks": {"rest": {"args": ["who", "price", "qty"], "do": [
        {"create": "order", "props": {"owner": "$who.id", "price": "$price", "qty": "$qty", "seq": "$world.trades"}},
        {"transfer": "cash", "from": "$who", "to": "$who", "amount": "$notional($price, $qty)", "into": "locked"}]}},
    "records": {"tape": {"fields": {"text": "text", "px": "number"}, "visible": "$it.author == $viewer.id"}},
    "actions": {
        "bid": {"by": "trader", "params": {"price": {"type": "number", "min": 1, "max": 50}, "qty": "int"},
                "do": [{"block": "rest", "with": {"who": "$actor", "price": "$params.price", "qty": "$params.qty"}},
                       {"post": "tape", "text": "{$actor.name} bid {$params.qty} @ {$params.price|money}", "px": "$params.price"}],
                "terminal": True},
        "note": {"by": "taker", "params": {"text": "text"}, "do": ["$entity(m).style = maker", "$actor.style = taker"]},
    },
    "stages": [{"name": "quote", "turns": "simultaneous"}],
    "views": {"book": {"for": "trader", "of": "order", "sort": "[$it.price, -$it.seq]", "desc": True, "bullet": False,
                       "show": "{price|money} x {qty}"},
              "mine": {"for": "trader", "show": "reserved {$reserved($actor)|money}; tape {$len($records(tape))}"}},
    "metrics": {"by_trader": "$dict(trader, $it.name, $it.locked)"},
    "outputs": {"locked": {"expr": "{Mo: $entity(m).locked, Tia: $entity(t).locked}", "type": "map"},
                "styles": {"expr": "$map(trader, $it.style)", "type": "list"},
                "books": {"expr": "$sum(trader, $count(order, $it.owner == $outer.id))", "type": "int"}},
}


def test_inheritance_defs_blocks_maps_and_outer_binding():
    issues = [i for i in fg_env.check(EXCHANGE) if i.severity == "error"]
    assert issues == []
    bids = {"m": (9, 2), "t": (11, 3)}
    result = fg_env.run(EXCHANGE, lambda w: w.call("bid", {"price": bids[w.entity_id][0], "qty": bids[w.entity_id][1]}), seed=1, rounds=1)
    env_view = None
    assert result.stats["actions"] == 2  # simultaneous commits are counted
    env = fg_env.load(EXCHANGE, seed=1)
    env.run(lambda w: w.call("bid", {"price": bids[w.entity_id][0], "qty": bids[w.entity_id][1]}), rounds=1)
    env_view = env.preview("m")
    assert "$33.00 x 3\n$9.00 x 2" not in env_view["update"]  # sorted by price, no bullets
    assert "$11.00 x 3\n$9.00 x 2" in env_view["update"]
    assert "reserved $18.00; tape 1" in env_view["update"]  # $records respects `visible` for the viewer
    final = env.run(lambda w: w.end())
    assert final.outputs["locked"] == {"Mo": 18.0, "Tia": 33.0}
    assert final.outputs["books"] == 2  # one resting order per trader, counted through $outer
    assert final.series["by_trader"][0] == {"Mo": 18.0, "Tia": 33.0}


def test_sealed_choice_that_cannot_happen_is_refused_at_submit():
    contract = copy.deepcopy(EXCHANGE)
    contract["types"]["trader"]["props"]["cash"] = 10.0
    notes = {}

    def agent(wake):
        first = wake.call("bid", {"price": 20, "qty": 1})
        second = wake.call("bid", {"price": 5, "qty": 1})
        notes[wake.entity_id] = [(first.ok, first.data.get("error"), first.text), (second.ok, second.text)]

    fg_env.run(contract, agent, seed=1, rounds=1)  # sealed turns run in threads: keep each agent's notes apart
    assert notes["m"][0] == (False, "rejected", "Mo has only 10 cash; 20 is needed.")
    assert notes["m"][1][0] is False  # refused by its `do`, the choice was spent, as when it commits


def test_inspect_is_scoped_by_type_rules():
    seen = {}

    def agent(wake):
        seen[wake.entity_id] = (wake.call("inspect", {"id": "t"}).text, wake.call("inspect", {"id": wake.entity_id}).text)
        wake.end()

    fg_env.run(EXCHANGE, agent, seed=1, rounds=1)
    assert seen["m"][0] == "No entity with that id is available to inspect. You can inspect: m."
    assert seen["t"][0].startswith("Tia [t] (taker)")


def test_checker_catches_enum_words_bare_prop_words_and_unknown_functions():
    bad = copy.deepcopy(EXCHANGE)
    bad["actions"]["note"]["when"] = ["$actor.style != nnone", "$notionl(1, 2) > 0", "cash > 3 and $actor.cash > 0"]
    bad["physics"] = {"vars": {"n": {"start": 1, "rate": "-n"}}, "read": {"n": "$count(trader)"}}
    with pytest.raises(ContractError) as info:
        fg_env.load(bad)
    text = str(info.value)
    assert "$actor.style is one of maker, taker, none; 'nnone' is not" in text and "did you mean 'none'" in text
    assert "unknown function $notionl" in text and "did you mean $notional" in text
    assert "'n' is also a variable or param" in text
    warnings = [str(i) for i in fg_env.check(bad) if i.severity == "warning"]
    assert any("bare word 'cash'" in w for w in warnings)


def test_none_is_text_and_general_assignment_targets():
    env = fg_env.load(EXCHANGE, seed=1)
    env.run({"t": lambda w: (w.call("note", {"text": "x"}), w.end()), "m": "idle"}, rounds=1)
    assert env.world.entities["m"].properties["style"] == "maker"
    assert env.world.entities["t"].properties["style"] == "taker"


def test_end_conditions_wait_for_the_stage_but_end_effect_is_immediate():
    game = {
        "name": "race", "clock": {"rounds": 3},
        "types": {"p": {"agent": True, "props": {"score": 0}}},
        "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
        "actions": {"step": {"by": "p", "do": ["$actor.score += 1",
                                              {"if": "$actor.score >= 2 and $actor.id == b", "then": [{"end": "b_first"}]}]}},
        "end": [{"when": "$any(p, $it.score >= 1)", "name": "someone_scored"}],
        "outputs": {"scores": {"expr": "$map(p, $it.score)", "type": "list"}},
    }
    result = fg_env.run(game, lambda w: w.call("step"), seed=1)
    assert result.ended_by == "someone_scored" and result.outputs["scores"] == [1, 1]  # b still moved this stage


def test_check_smoke_round_and_cli_preview_after_rounds(tmp_path, capsys):
    broken = copy.deepcopy(EXCHANGE)
    broken["views"]["mine"]["show"] = "{$entity(nobody).cash}"
    issues = fg_env.check(broken, rounds=1)
    assert any(i.path.startswith("views.mine") for i in issues if i.severity == "error")
    path = tmp_path / "x.json"
    path.write_text(json.dumps(EXCHANGE))
    assert main(["preview", str(path), "m", "--rounds", "1", "--agent", "idle"]) == 0
    assert "Round 2 of 2 · quote" in capsys.readouterr().out  # the next real turn, after one played round


def test_checker_refuses_a_number_compared_with_a_bare_word():
    c = {"name": "Typos", "clock": {"rounds": 2}, "world": {"price": 5},
         "types": {"buyer": {"agent": True, "props": {"cash": 10}}}, "entities": {"a": {"type": "buyer"}},
         "actions": {"buy": {"by": "buyer", "when": "$actor.cash > price", "do": "$actor.cash -= $world.price"}}}
    with pytest.raises(ContractError, match="is a number, compared with the text 'price'"):
        fg_env.load(c)
