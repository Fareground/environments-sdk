"""The compiled expression language gives exactly what the closure evaluator it replaced gave.

Checked against the reference evaluator kept in expr_oracle.py:

* compiling — every expression in every example contract (conditions, effect statements and their targets,
  template parts), the grammar fuzz corpus and hand-picked edge cases compile to the same facts (roots, paths,
  calls, …) or fail with the same error;
* evaluating — every example contract's run, random playouts of every example game (legal listings, dry runs,
  sampling) and the grammar fuzz's evaluations and effects evaluate every expression both ways (see expr_dual.py)
  with the same value, error, work-budget charge, random draws and def cache.

``FG_ENV_EXPR_ORACLE=1 pytest tests`` runs the whole suite the same way.
"""
import json
import random
from pathlib import Path

import pytest
from expr_dual import MISMATCHES, dual_evaluation
from expr_oracle import compile_oracle
from test_hardening_security import (
    FUZZ_CASES, FUZZ_SEED, _expr, _mangle, _statement, _world,
)

import fg_env
from fg_env.entity import Entity
from fg_env.actions import ActionSpec
from fg_env.effects import split_statement, statement_parts
from fg_env.errors import RunError
from fg_env.expr import ExprError, Scope, Untrusted, compile_expr
from fg_env.game.steps import apply_step, random_step
from fg_env.template import compile_template, render

pytestmark = pytest.mark.slow  # statistical or engine-behaviour: `make test-fast` leaves it out

CONTRACTS = Path(__file__).parents[1] / "examples" / "contracts"
EXAMPLES = sorted(CONTRACTS.glob("*.json"))
GAMES = sorted((CONTRACTS / "games").glob("*.json"))
FACTS = ("roots", "functions", "symbols", "paths", "calls", "item_paths", "comparisons", "item_comparisons",
         "arity_errors", "methods")
EDGE_CASES = [
    "", "   ", "$", "$1x", "$x.", "$x._secret", "x.y", "$f", "$count", "$count()", "$count(a, b, c, d)", "$nope(1)",
    "$max(1, 2, 3)", "$len(1, 2)", "true.x", "null[0]", "(3).x", "[1, 2][true]", "{1: 2, 'a': 3, b: 4, 'b': 5}",
    "{$x: 1}", "{true: 1}", "1j", "b'x'", "...", "$x if $y", "lambda: 1", "[i for i in $x]", "$x[1:2]", "not not not $x",
    "-$x", "+$x", "--1", "1 < $x < 3 < $y", "$x == $y == $z", "$x in $y not in $z", "$a and $b or $c and not $d",
    "10 ** 10 ** 10", "(10 ** 1000) ** 1000", "2 ** 62 * 2 ** 62", "7 // 0", "7 % 0", "7 / 0", "1.5 // 0.5", "10 ** 400 / 1",
    "$x.count", "'a' + 'b'", "'a' * 3", "[1] + [2]", "'x' in 'xyz'", "1 in 'xyz'", "$x.id", "$x.at", "$x.type",
    "$world.board[$it[0]] == $m and $world.board[$it[1]] == $m", "$filter(player, $it.id != $p.id)[0]",
    "$it.owner == $outer.id and $chance(0.5)", "$pick(item, $it.worth > $randint(0, 2))",
    "1 + " * 600 + "1", "(" * 150 + "1" + ")" * 150, "not " * 700 + "true", "$x if $x else " * 60 + "0",
    "$a and " * 90 + "$b", "[" * 90 + "]" * 90, "$abs(" * 40 + "1" + ")" * 40, "$count(" * 30 + "x" + ")" * 30,
    "$pattern.season($it.sku)", "$pattern.level()", "$x.y(1, $z)", "$x.y.z(1)", "$f.g", "$count.x(1)", "(1).x(2)",
    "$x._y(1)", "$x.y(k=1)", "&& || !", "$x && !$y || $z", "$'text' + $(1 + 2)", "'unclosed", "\x00",
]


def _contract_texts(value, out):
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, list):
        for item in value:
            _contract_texts(item, out)
    elif isinstance(value, dict):
        for item in value.values():
            _contract_texts(item, out)


def _expressions(text):
    """Every expression a contract string may hold: itself, an assignment's parts, a template's parts."""
    found = {text}
    if split_statement(text) is not None:
        try:
            base, steps, local, _, right = statement_parts(text)
            found |= {right} | ({base} if base else set()) | {step for kind, step in steps if kind == "index"}
        except ExprError:
            pass
    for subject in (None, "it"):
        try:
            found |= {expression.source for expression in compile_template(text, subject).expressions}
        except ExprError:
            pass
    return found


def _corpus():
    texts = set(EDGE_CASES)
    for path in EXAMPLES + GAMES + sorted(CONTRACTS.glob("*/*.json")):
        raw = set()
        _contract_texts(json.loads(path.read_text()), raw)
        for text in raw:
            texts |= _expressions(text)
    rng = random.Random(FUZZ_SEED)
    texts |= {_mangle(rng, _expr(rng, rng.randint(1, 4))) for _ in range(FUZZ_CASES)}
    return sorted(texts)


def _compiled(compile, source):
    try:
        found = compile(source)
    except ExprError as exc:
        return "error", (str(exc), exc.detail, exc.source)
    return "facts", tuple(getattr(found, fact) for fact in FACTS)


def test_every_expression_compiles_to_the_same_facts_or_fails_the_same_way():
    corpus = _corpus()
    assert len(corpus) > 2_000
    differ = [(source, _compiled(compile_oracle, source), _compiled(compile_expr.__wrapped__, source))
              for source in corpus]
    assert [entry for entry in differ if entry[1] != entry[2]] == []


@pytest.fixture
def both_ways():
    start = len(MISMATCHES)
    with dual_evaluation():
        yield
    assert MISMATCHES[start:] == []


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_every_example_contract_evaluates_identically(path, both_ways):
    result = fg_env.load(path, seed=7).run(rounds=2)
    assert result.status in ("running", "completed", "ended"), result.error


@pytest.mark.parametrize("path", GAMES, ids=[p.stem for p in GAMES])
def test_every_example_game_lists_and_plays_identically(path, both_ways):
    rng = random.Random(3)
    subject = fg_env.rl.game(path)
    for playout in range(3):
        state = subject.new_initial_state()
        while not state.is_terminal():
            if playout == 2 and not state.is_chance_node() and not state.is_simultaneous_node():
                state.apply_action(state.sample_legal_action(rng))
            else:
                apply_step(state, random_step(state, rng))
        state.close()


_OPERANDS = [0, 1, -7, 3, 2 ** 70, -(2 ** 63), 1.5, -0.0, float("inf"), float("nan"), True, False, None, "ab", "",
             Untrusted("ab"), [], [1, "a"], {}, {"k": 1, "1": 2}]
_OPERATORS = ["+", "-", "*", "/", "//", "%", "**", "==", "!=", "<", "<=", ">", ">=", "in", "not in", "and", "or"]


def test_every_operator_on_every_kind_of_operand_evaluates_identically(both_ways):
    entity = Entity("e1", "Eve", "thing", {"cash": 3, "tag": "ab"})
    operands = _OPERANDS + [entity]
    for a in operands:
        scope = Scope({"a": a, "e": entity, "l": [1, 2, 3], "m": {"k": 1, "ab": 2}})
        for text in ("-$a", "+$a", "not $a", "$l[$a]", "$m[$a]", "$e[$a]", "$a[0]", "$a.k", "$a.id", "$a.cash",
                     "$e.cash + $a", "$e.tag == $a", "$a if $a else 0", "1 < $a < 3", "$a.count"):
            _evaluate(text, scope)
        for b in operands:
            scope = Scope({"a": a, "b": b})
            for op in _OPERATORS:
                _evaluate(f"$a {op} $b", scope)


_THINGS = {
    "name": "Things",
    "types": {"thing": {"props": {"cash": 0, "tag": {"type": "text", "default": ""}}}},
    "entities": {"a": {"type": "thing", "props": {"cash": 1, "tag": "ab"}}, "b": {"type": "thing", "props": {"cash": 3}},
                 "c": {"type": "thing", "props": {"cash": 0, "tag": "ab"}}},
    "defs": {"total": {"expr": "$sum(thing, $it.cash)"}},
}
_LOOP_CALLS = ["$any({items}, {condition})", "$all({items}, {condition})", "$count({items}, {condition})",
               "$filter({items}, {condition})", "$pick({items}, {condition})"]
_LOOP_ITEMS = ["thing", "$l", "$m", "$e", "null", "7", "'nope'", "[]", "$range(4)"]
_LOOP_CONDITIONS = ["$it.cash > 1", "$it.cash == $x", "$x == $it.cash", "$it.cash == $missing", "$it.id == $e.id",
                    "$it == $x", "$i > 0 and $it", "$outer", "$missing", "$it.nope", "$count($l, $it > $outer)",
                    "$chance(0.5)", "$it.cash / ($i - 1)", "$total > $i", "$it.tag == 'ab' or $random() < 0.3",
                    "$it.cash if $it else $x", "$any($l, $it == $outer.cash)"]


@pytest.mark.parametrize("shadowed", [False, True], ids=["built-in", "shadowed by a def"])
def test_inlined_collection_loops_evaluate_identically(both_ways, shadowed):
    contract = dict(_THINGS, defs={**_THINGS["defs"], "any": {"args": ["a", "b"], "expr": "7"}}) if shadowed else _THINGS
    world = fg_env.load(contract, seed=3).world
    scope = world.scope(x=1, l=[1, 2, 3], m={"a": 1, "b": 0}, e=world.entities["a"], it=world.entities["b"])
    for call in _LOOP_CALLS:
        for items in _LOOP_ITEMS:
            for condition in _LOOP_CONDITIONS:
                _evaluate(call.format(items=items, condition=condition), scope)



@pytest.mark.parametrize("shadowed", [False, True], ids=["built-in", "shadowed by a def"])
def test_mapped_aggregates_preserve_evaluation_order_and_scope(both_ways, shadowed):
    contract = dict(_THINGS, defs={**_THINGS["defs"], **{
        name: {"args": ["a", "b", "c"], "expr": "7"} for name in ("sum", "avg")
    }}) if shadowed else _THINGS
    world = fg_env.load(contract, seed=3).world
    scope = world.scope(l=[1, 2, 3], it=world.entities["b"])
    for name in ("sum", "avg"):
        for items in ("thing", "$l", "[]", "null", "7", "{a: 1, b: 2}"):
            for value in ("$it.cash", "$i", "$outer.cash + $i", "null", "'invalid'", "$random()",
                          "$it / ($i - 1)", "$sum($l, $it + $outer.cash)", "$missing"):
                _evaluate(f"${name}({items}, {value})", scope)
                for condition in ("true", "false", "$i > 0", "$it.cash == 1", "$chance(0.5)", "$missing"):
                    _evaluate(f"${name}({items}, {value}, {condition})", scope)

def _evaluate(text, scope):
    try:
        compile_expr(text)(scope)
    except ExprError:
        pass


def test_the_grammar_fuzz_evaluates_identically(both_ways):
    rng = random.Random(FUZZ_SEED)
    env = fg_env.load(_world(), seed=1)
    world = env.world
    actor = world.entities["ann"]
    params = {"s": Untrusted("hi «there»"), "n": 7, "l": [1, "a", Untrusted("u")], "m": {"k": Untrusted("v")},
              "nan": float("nan")}
    for _ in range(FUZZ_CASES):
        source = _mangle(rng, _expr(rng, rng.randint(1, 4)))
        effects = [_statement(rng) for _ in range(rng.randint(1, 2))]
        try:
            scope = world.scope(actor=actor, params=params, it=rng.choice([actor, 3, params["s"], None]), i=0)
            compile_expr(source)(scope)
            render("{" + source + "}" + rng.choice(["", "|money", "|pct"]), scope, None)
        except (ExprError, RunError):
            pass
        env.contract.actions["fuzz"] = ActionSpec(by="person", do=effects)
        try:
            env.actions.apply(actor, "fuzz", params)
        except (ExprError, RunError):
            pass
