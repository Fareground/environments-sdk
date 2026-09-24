import random

import pytest

from fg_env.world.entity import Entity
from fg_env.expr import ExprError, Scope, World, compile_expr, evaluate, is_expr, resolve
from fg_env.expr.template import render


class _World(World):
    def __init__(self, entities):
        self.rng = random.Random(7)
        self._entities = {e.id: e for e in entities}

    def entities_of(self, type_name):
        return [e for e in self._entities.values() if e.entity_type == type_name and e.alive]

    def entity(self, entity_id):
        return self._entities.get(entity_id)

    def is_type(self, name):
        return any(e.entity_type == name for e in self._entities.values())


def _scope(**vars):
    offers = [
        Entity("a", "Alpha", "offer", {"price": 10, "stock": 3, "rating": 4.5}),
        Entity("b", "Beta", "offer", {"price": 25, "stock": 0, "rating": 4.9}),
        Entity("c", "Gamma", "offer", {"price": 5, "stock": 9, "rating": 3.1}),
    ]
    buyer = Entity("u1", "Uma", "buyer", {"cash": 40, "status": "open"})
    world = _World(offers + [buyer])
    base = {"actor": buyer, "params": {"offer": offers[0], "qty": 2}, "round": 3}
    base.update(vars)
    return Scope(base, world)


def test_arithmetic_and_paths():
    s = _scope()
    assert evaluate("$actor.cash - $params.offer.price * $params.qty", s) == 20
    assert evaluate("$round / 2", s) == 1.5
    assert evaluate("$actor.cash >= 40 && !($round > 5)", s) is True
    assert evaluate("$params.qty if $actor.cash > 100 else 1", s) == 1


def test_symbols_are_text():
    s = _scope()
    assert evaluate("$actor.status == open", s) is True
    assert evaluate("$actor.status in [open, closed]", s) is True
    assert evaluate("$actor.status == 'open'", s) is True


def test_aggregates_with_item_binding():
    s = _scope()
    assert evaluate("$count(offer)", s) == 3
    assert evaluate("$count(offer, $it.stock > 0)", s) == 2
    assert evaluate("$sum(offer, $it.price * $it.stock)", s) == 75
    assert evaluate("$avg(offer, $it.price, $it.stock > 0)", s) == 7.5
    assert [e.id for e in evaluate("$top(offer, $it.rating, 2)", s)] == ["b", "a"]
    assert evaluate("$pick(offer, $it.price < 8).name", s) == "Gamma"
    assert evaluate("$max(offer, $it.price)", s) == 25
    assert evaluate("$max(3, 9, 4)", s) == 9
    assert evaluate("$params.offer in $filter(offer, $it.stock > 0)", s) is True


def test_entity_equality_accepts_ids():
    s = _scope()
    assert evaluate("$params.offer == a", s) is True
    assert evaluate("$params.offer.id == 'a'", s) is True


def test_strict_errors_name_the_fix():
    s = _scope()
    with pytest.raises(ExprError, match="has no property 'cashh'"):
        evaluate("$actor.cashh > 0", s)
    with pytest.raises(ExprError, match="not available here"):
        evaluate("$target.cash", s)
    with pytest.raises(ExprError, match="did you mean \\$count"):
        evaluate("$cuont(offer)", s)  # unknown names may be contract `defs`, so this is a run-time error
    with pytest.raises(ExprError, match="division by zero"):
        evaluate("$actor.cash / 0", s)
    with pytest.raises(ExprError, match="expected a number"):
        evaluate("$actor.status + 1", s)
    with pytest.raises(ExprError, match="not an entity type"):
        evaluate("$count(ofer)", s)


def test_no_python_escape():
    for bad in ["__import__('os')", "$actor.__class__", "(lambda: 1)()", "[x for x in [1]]", "open('x')"]:
        with pytest.raises(ExprError):
            compile_expr(bad)


def test_compile_reports_roots_and_functions():
    expr = compile_expr("$sum(offer, $it.price) > $inputs.budget")
    assert expr.roots == {"inputs"}  # $it is bound by $sum itself
    assert expr.functions == {"sum"}
    assert "offer" in expr.symbols
    assert ("sum", "offer", ("it", "price")) in expr.item_paths


def test_random_is_seeded_by_world():
    a = [evaluate("$randint(1, 100)", _scope()) for _ in range(3)]
    b = [evaluate("$randint(1, 100)", _scope()) for _ in range(3)]
    assert a == b


def test_resolve_deep_and_is_expr():
    s = _scope()
    assert is_expr("$actor.cash") and not is_expr("costs 5 dollars") and not is_expr(5)
    assert resolve({"a": "$round", "b": ["$actor.cash", "plain"]}, s) == {"a": 3, "b": [40, "plain"]}


def test_templates():
    s = _scope(it=_scope().world.entity("a"))
    assert render("[{id}] {name} · {price|money} · {rating|1}★", s) == "[a] Alpha · $10.00 · 4.5★"
    assert render("{$count(offer, $it.stock > 0)} in stock, day {$round}", s) == "2 in stock, day 3"
    assert render("You have {cash|money}.", s, subject="actor") == "You have $40.00."
    assert render("{{literal}}", s) == "{literal}"
    with pytest.raises(ExprError, match="unknown format"):
        render("{price|dollars}", s)



@pytest.mark.parametrize('source,expected', [
    ('!($round > 5)', True),
    ('!($round < 5)', False),
    ('!!($round < 5)', True),
    ('!($round > 5) && $actor.cash >= 40', True),
])
def test_boolean_negation_can_start_an_expression(source, expected):
    assert evaluate(source, _scope()) is expected
