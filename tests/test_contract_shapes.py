"""A malformed contract is told where it is wrong: every field reading it walks is shape-checked first, so `check`
never raises and `load` raises only `ContractError`, whatever a field holds (audit 11 M1)."""
import copy
import json
import random
from pathlib import Path

import pytest

import fg_env

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
