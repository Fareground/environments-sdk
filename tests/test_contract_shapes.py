"""A malformed contract is told where it is wrong: every field reading it walks is shape-checked first, so `check`
never raises and `load` raises only `ContractError`, whatever a field holds (audit 11 M1)."""
import copy
import json
import random
from pathlib import Path

import _fuzz
import pytest

import fg_env
from fg_env.effects.runner import select_ops
from fg_env.effects.shapes import EFFECT_FIELDS

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"
BASES = [json.loads((EXAMPLES / f"{name}.json").read_text()) for name in ("werewolf", "auction_house", "kuhn_poker")]
#: What a mutation writes into a field: every JSON shape, and texts that are almost expressions.
ODD = [None, True, 0, -1, 1.5, "", "$", "${", "$x +", [], {}, [None], {"a": None}, "all", ["p"], {"expr": "$"},
       10 ** 30]


def _contract(**changes):
    c = {"name": "Shapes", "clock": {"rounds": 1},
         "types": {"p": {"agent": True, "props": {"cash": 1}}},
         "entities": {"a": {"type": "p"}},
         "records": {"chat": {"fields": {"text": "text"}}},
         "actions": {"talk": {"by": "p", "do": [{"post": "chat", "text": "'hi'"}]},
                     "spawn": {"by": "p", "do": [{"create": "p", "props": {"cash": 2}}]}},
         "stages": [{"name": "s"}]}
    for path, value in changes.items():
        target = c
        *steps, last = path.split(".")
        for step in steps:
            target = target[int(step)] if isinstance(target, list) else target[step]
        target[int(last) if isinstance(target, list) else last] = value
    return c


@pytest.mark.parametrize("change, path", [
    ({"actions.talk.do.0.post": ["chat"]}, "actions.talk.do[0].post"),
    ({"actions.talk.do.0.post": None}, "actions.talk.do[0].post"),
    ({"actions.spawn.do.0.props": []}, "actions.spawn.do[0].props"),
    ({"actions.spawn.do.0.create": 3}, "actions.spawn.do[0].create"),
    ({"types.p.props": True}, "types.p.props"),
    ({"stages.0.actions": True}, "stages[0].actions"),
    ({"stages.0.actions": None}, "stages[0].actions"),
    ({"stages.0.name": ["s"]}, "stages[0].name"),
    ({"mechanisms": {"m": {"kind": "market", "mode": ["auction"]}}}, "mechanisms.m.mode"),
])
def test_a_malformed_field_is_an_error_at_its_path(change, path):
    contract = _contract(**change)
    issues = fg_env.check(contract, rounds=0)
    assert any(i.severity == "error" and i.path == path for i in issues), [str(i) for i in issues]
    with pytest.raises(fg_env.ContractError):
        fg_env.load(contract)


def _paths(value, path=()):
    yield path
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _paths(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _paths(item, (*path, index))


def test_no_mutation_of_a_contract_makes_check_or_load_raise_anything_but_a_contract_error():
    rng = random.Random(11)
    for _ in range(60):
        contract = copy.deepcopy(rng.choice(BASES))
        path = rng.choice([p for p in _paths(contract) if p])
        target = contract
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = rng.choice(ODD)
        assert isinstance(fg_env.check(contract, rounds=0), list), path
        try:
            fg_env.load(contract)
        except (fg_env.ContractError, fg_env.InputError):
            pass


#: The keys whose values are effect lists (in actions, events, defs, and nested in effects).
_EFFECT_LISTS = ("do", "then", "else")


def _effect_fields(value, path=(), in_effects=False):
    """Every ``(path, op, key)`` of a field of a core operation object in the contract's effect lists."""
    if isinstance(value, dict):
        ops = select_ops(value) if in_effects else []
        if len(ops) == 1 and ops[0] in EFFECT_FIELDS:
            yield from (((*path, key), ops[0], key) for key in value if key in EFFECT_FIELDS[ops[0]])
        for key, item in value.items():
            yield from _effect_fields(item, (*path, key), key in _EFFECT_LISTS)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _effect_fields(item, (*path, index), in_effects)


def _dotted(path):
    return "".join(f"[{step}]" if isinstance(step, int) else f".{step}" for step in path).lstrip(".")


def _mutated_contracts():
    names = sorted(p.stem for p in EXAMPLES.glob("*.json"))
    yield from ((name, json.loads((EXAMPLES / f"{name}.json").read_text())) for name in names)
    yield from ((f"fuzz-{seed}", _fuzz.contract(seed)) for seed in range(6))


@pytest.mark.parametrize("name, contract", list(_mutated_contracts()), ids=lambda v: v if isinstance(v, str) else "")
def test_every_effect_field_is_shape_checked_as_the_runner_reads_it(name, contract, monkeypatch):
    """Every field of every effect, mutated to an odd value: check never raises, and a value whose shape the runner
    would read differently (a list as a condition, null as a loop) is always an error at that field (audit 12 H2)."""
    monkeypatch.chdir(EXAMPLES)  # where an example's imports are
    for count, (path, op, key) in enumerate(_effect_fields(contract)):
        mutated = copy.deepcopy(contract)
        target = mutated
        for step in path[:-1]:
            target = target[step]
        odd = ODD[count % len(ODD)]
        target[path[-1]] = copy.deepcopy(odd)
        issues = fg_env.check(mutated, rounds=0)
        if not EFFECT_FIELDS[op][key].fits(odd):
            at = _dotted(path[:-1])  # the effect or its field (a mechanism's effects are checked where it puts them)
            assert any(i.severity == "error" and (i.path.startswith(at) or i.path.endswith(f".{key}"))
                       for i in issues), (name, _dotted(path), odd)


@pytest.mark.parametrize("edge", [[["a"], "b"], {"from": {"x": 1}, "to": "b"}, ["a", "b", "c"], {"from": "a", "to": "b",
                                                                                                "weight": "far"},
                                  {"from": "a", "to": "b", "wieght": 2}])
def test_a_malformed_graph_edge_is_an_error_not_a_crash(edge):
    """The checker and the built space read an edge through one function (audit 12 M1)."""
    contract = _contract(space={"graph": {"nodes": ["a", "b"], "edges": [edge]}})
    issues = fg_env.check(contract, rounds=0)
    assert any(i.severity == "error" and i.path == "space.graph.edges[0]" for i in issues), [str(i) for i in issues]


@pytest.mark.parametrize("field", ["id", "name", "type", "alive", "at"])
def test_assigning_a_built_in_entity_field_is_an_error_at_check(field):
    """An entity's built-in fields are read-only, said statically and at run time alike (audit 12 M6)."""
    contract = _contract(**{"actions.talk.do": [f"$actor.{field} = 'zz'"]})
    issues = fg_env.check(contract, rounds=0)
    assert any(i.severity == "error" and "built into every entity" in i.message for i in issues), \
        [str(i) for i in issues]
