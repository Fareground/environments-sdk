"""Capabilities and fixes from the second LLM side-agent stress round."""
import json

import pytest

import fg_env
from fg_env.expr import Scope, evaluate

CHAIN = {
    "name": "Mini chain",
    "clock": {"rounds": 3, "unit": "year", "start": "2025-01-01", "step": 5},
    "world": {"now": 0, "total_stock": {"type": "number", "default": "$sum(tier, $it.stock)"},
              "board": {"type": "list", "default": [0, 0, 0]}, "tally": {"type": "map", "default": {"a": 1}}},
    "types": {
        "tier": {"agent": True, "policy": "steady", "props": {"stock": 10, "n": 0, "log": {"type": "list", "default": []}}},
        "shop": {"extends": "tier"},
        "plant": {"extends": "tier"},
    },
    "entities": {"s": {"type": "shop", "name": "Shop"}, "p": {"type": "plant", "name": "Plant"}},
    "defs": {"year": "$clock.date"},
    "actions": {
        "order": {"by": "tier", "params": {"qty": {"type": "int", "min": 0, "max": 20, "invalid": "Order 0 to 20 units, not {$value}."}},
                  "do": ["$actor.n += 1", "$world.board[$actor.n - 1] = $params.qty", "$world.tally[$actor.id] += $params.qty",
                         "$actor.log += $params.qty"],
                  "terminal": "$actor.n >= 2"},
    },
    "stages": [{"name": "orders", "must_act": True, "max_actions": 3, "on_idle": ["$actor.stock -= 1"]}],
    "events": [{"phase": "start", "do": ["$world.now = $round"]}],
    "views": {"status": {"for": "tier", "title": "Status in {$year}", "of": "tier", "show": "{name} {stock|pct1}"}},
    "policies": {"steady": {"rules": [{"do": "order", "with": {"qty": 4}}]}},
    "outputs": {"orders": {"expr": "$flatten($map(tier, $it.log))", "type": "list"},
                "fallback": {"expr": "$get($world.tally, zz) or 0", "type": "number"}},
}


def _contract(**changes):
    data = json.loads(json.dumps(CHAIN))
    data.update(changes)
    return data


def test_parent_participant_keys_and_inherited_policy():
    seen = []
    fg_env.run(_contract(), {"tier": lambda w: (seen.append(w.type), w.call("order", {"qty": 1}), w.call("order", {"qty": 1}))},
               seed=1, rounds=1)
    assert sorted(seen) == ["plant", "shop"]
    result = fg_env.run(_contract(outputs={}), seed=1, rounds=1)  # no participants: the parent's policy
    assert result.stats["actions"] == 2


def test_world_defaults_that_read_entities_see_the_population():
    env = fg_env.load(_contract(), seed=1)
    assert env.props["total_stock"] == 20


def test_element_assignment_terminal_expression_and_flatten():
    def agent(wake):
        first = wake.call("order", {"qty": 3})
        second = wake.call("order", {"qty": 5})
        third = wake.call("order", {"qty": 7})
        assert not first.ended and second.ended and third.data["error"] == "ended"

    env = fg_env.load(_contract(), seed=1)
    result = env.run(agent, rounds=1)
    assert env.props["board"] == [3, 5, 0]
    assert env.props["tally"] == {"a": 1, "s": 8, "p": 8}
    assert result.outputs == {"orders": [3, 5, 3, 5], "fallback": 0}  # provisional outputs mid-run


def test_must_act_on_idle_and_invalid_text():
    notes = []

    def lazy(wake):
        notes.append([t.name for t in wake.tools])
        notes.append(wake.end().text)
        notes.append(wake.call("order", {"qty": 99}).text)

    env = fg_env.load(_contract(), seed=1)
    env.run({"s": lazy, "p": "idle"}, rounds=1)
    assert "end_turn" not in notes[0]
    assert notes[1].startswith("You must act during orders")
    assert notes[2] == "order was not done: Order 0 to 20 units, not 99. Correct the arguments and call again."
    assert env.entity("p")["props"]["stock"] == 9  # on_idle ran for the agent that never acted


def test_preview_shows_the_next_real_turn():
    env = fg_env.load(_contract(), seed=1)
    view = env.preview("s")
    assert view["update"].startswith("Year 1 of 3 (2025) · orders")
    assert "Status in 2025:" in view["update"] and "Shop 1000.0%" in view["update"]
    env.run(rounds=1)
    assert env.preview("s")["update"].startswith("Year 2 of 3 (2030)")
    assert env.props["now"] == 1  # the preview played round 2's start on a copy only


def test_zero_argument_defs_values_and_or_defaults():
    assert evaluate("$x or 0", Scope({"x": None})) == 0
    assert evaluate("$x and $y", Scope({"x": 2, "y": 3})) == 3
    assert [i for i in fg_env.check(_contract()) if i.severity == "error"] == []


def test_wake_me_and_env_accessors():
    captured = {}

    def agent(wake):
        captured.update(wake.me)
        wake.call("order", {"qty": 1})
        wake.call("order", {"qty": 1})

    env = fg_env.load(_contract(), seed=1)
    env.run(agent, rounds=1)
    assert captured["id"] in ("s", "p") and "at" in captured and captured["stock"] == 10
    assert {e["id"] for e in env.entities("tier")} == {"s", "p"}


def test_parallel_experiment_in_processes_matches_sequential():
    one = fg_env.experiment(_contract(), runs=3, participants="random", workers=1)
    many = fg_env.experiment(_contract(), runs=3, participants="random", workers=2)
    assert one.arms["baseline"].outputs == many.arms["baseline"].outputs


NETWORK = {
    "name": "Followers", "clock": {"rounds": 1},
    "types": {"user": {"agent": True, "props": {"star": False}}},
    "population": [{"type": "user", "count": 30, "props": {"star": "$i <= 3"}}],
    "relations": {"follows": {}},
    "links": [{"relation": "follows", "among": "user", "graph": "random", "p": "0.9 if $to.star else 0.05"}],
    "actions": {"wait": {"by": "user", "do": []}},
    "outputs": {"star_followers": {"expr": "$avg(filter(user, $it.star), $count(user, $linked($it, $outer, follows)))".replace("filter(", "$filter("), "type": "number"}},
}


def test_directed_random_graph_with_per_pair_probability():
    env = fg_env.load(NETWORK, seed=4)
    edges = env.world.links["follows"]
    assert any((a, b) in edges and (b, a) not in edges for a, b in edges)  # one-way links exist
    stars = {e["id"] for e in env.entities("user") if e["props"]["star"]}
    into_stars = sum(1 for (_, b) in edges if b in stars) / len(stars)
    into_others = sum(1 for (_, b) in edges if b not in stars) / (30 - len(stars))
    assert into_stars > 5 * into_others


@pytest.mark.parametrize("graph", ["random", "ring", "small_world"])
@pytest.mark.parametrize("one_way", [True, False])
def test_degree_is_the_mean_number_of_neighbours_in_every_graph(graph, one_way):
    contract = {"name": "Degree", "clock": {"rounds": 1}, "types": {"person": {}},
                "population": [{"type": "person", "count": 600}],
                "relations": {"knows": {} if one_way else {"symmetric": True}},
                "links": [{"relation": "knows", "among": "person", "graph": graph, "degree": 4}]}
    neighbours = {}
    for a, b in fg_env.load(contract, seed=3).world.links["knows"]:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    assert sum(len(v) for v in neighbours.values()) / 600 == pytest.approx(4, rel=0.06)


def test_quote_marker_is_accepted_inside_expressions():
    assert evaluate("($'yes' if $x else 'no') + $'!'", Scope({"x": True})) == "yes!"
