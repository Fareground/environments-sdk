"""Every id an effect names resolves to one of the world's entities, or the effect fails: never a silent write.

The fields that name entities are one table (``effects.shapes.ENTITY_KEYS``): a recipient, a link's ends, a party to a
transfer, what is moved, removed or woken, an entry's author. For each of them this test writes values that are no
entity — ids of nobody, an entity's name instead of the entity, empty text, numbers, maps, lists holding one of those —
as the literal the author wrote and as what an expression computes. A literal id is a check error at its path; a
computed one refuses the agent's action at run time, and the world stays exactly as it was (audit 13 H2: a post `to`
an entity's name went to nobody, a link to it stored an edge no neighbour read finds). An `end`'s winner is an entity,
a list of them or a side's name, and nothing else (audit 13 L4).
"""
import copy
import random

import pytest
from _corpus import restored_state

import fg_env
from fg_env.effects.shapes import ENTITY_KEYS

#: A valid effect of each operation with an entity field (every field set to something that works).
BASE = {
    "remove": {"remove": "$entity(t1)"},
    "transfer": {"transfer": "coins", "from": "$actor", "to": "$entity(b)", "amount": 1},
    "link": {"link": "knows", "from": "$actor", "to": "$entity(b)"},
    "unlink": {"unlink": "knows", "from": "$actor", "to": "$entity(b)"},
    "move": {"move": "$actor", "to": [0, 1]},
    "post": {"post": "notes", "text": "psst", "to": "$entity(b)", "author": "$actor"},
    "emit": {"emit": "news", "say": "Psst.", "to": "$entity(b)"},
    "wake": {"wake": "$entity(b)", "why": "Look."},
}
#: What an expression may give where an entity belongs and is none: an id of nobody, a name, empty text, a number,
#: a map; and a list holding one of them.
COMPUTED = ["$params.bad", "$actor.name", "$params.bad + ''", "$round", "$round > 0", "$dict(p, $it.id, 1)",
            "[$actor, $params.bad]", "[$round]"]
#: What a contract may write where an entity belongs and is none, as a literal.
LITERAL = ["zed", "Bob", ["b", "zed"]]


def _contract(effect):
    return {"name": "Ids", "clock": {"rounds": 1},
            "space": {"grid": {"rows": 1, "cols": 3}},
            "types": {"p": {"agent": True, "props": {"coins": 10}}, "tok": {"props": {}}},
            "entities": {"a": {"type": "p", "name": "Ann", "at": [0, 0]}, "b": {"type": "p", "name": "Bob"},
                         "t1": {"type": "tok"}},
            "relations": {"knows": {"links": [{"from": "a", "to": "b"}]}},
            "records": {"notes": {"fields": {"text": "text"}}},
            "actions": {"go": {"by": "p", "description": "Go.", "params": {"bad": {"type": "text"}}, "do": [effect]}}}


def _fields():
    for op, keys in ENTITY_KEYS.items():
        assert op in BASE, f"the test has no working `{op}` to break"
        for key in keys:
            yield op, key


def _refused(contract, key, bad):
    """Whether a value that is no entity in ``key`` is refused: by the checker at its field, or at run time — the
    action refused and the world as it was."""
    errors = [issue for issue in fg_env.check(contract, rounds=0) if issue.severity == "error"]
    if errors:
        assert all(issue.path.startswith(f"actions.go.do[0].{key}") for issue in errors), errors
        return True
    result, unchanged = _call(contract, bad)
    return not result.ok and unchanged


def _call(contract, bad):
    """Call ``go`` as a (with ``bad`` as its text argument) on a fresh run; the result and whether the world stayed
    as it was."""
    env = fg_env.load(contract, seed=1)
    seen = {}

    def agent(wake):
        if "result" not in seen:
            before = restored_state(env)
            seen["result"] = wake.call("go", {"bad": bad})
            seen["unchanged"] = restored_state(env) == before
        wake.end()

    result = env.run({"a": agent, "b": lambda wake: wake.end()})
    assert result.error is None, result.error
    return seen["result"], seen["unchanged"]


def test_the_working_effects_work():
    for op in BASE:
        result, _ = _call(_contract(BASE[op]), "b")
        assert result.ok, (op, result.text)


@pytest.mark.parametrize(("op", "key"), list(_fields()), ids=lambda part: str(part))
def test_a_literal_id_of_no_entity_is_a_check_error_at_its_field(op, key):
    for literal in LITERAL:
        effect = {**copy.deepcopy(BASE[op]), key: literal}
        errors = [issue for issue in fg_env.check(_contract(effect), rounds=0) if issue.severity == "error"]
        assert any(issue.path.startswith(f"actions.go.do[0].{key}") for issue in errors), (op, key, literal, errors)


@pytest.mark.parametrize(("op", "key"), list(_fields()), ids=lambda part: str(part))
def test_a_computed_id_of_no_entity_refuses_the_action_and_changes_nothing(op, key):
    for computed in COMPUTED:
        for bad in ("zed", "Bob", ""):
            effect = {**copy.deepcopy(BASE[op]), key: computed}
            assert _refused(_contract(effect), key, bad), (op, key, computed, bad)


@pytest.mark.slow
def test_random_non_entities_in_random_entity_fields_never_write():
    rng = random.Random(13)
    fields = list(_fields())
    for _ in range(200):
        op, key = rng.choice(fields)
        value = rng.choice([*COMPUTED, f"[{rng.choice(COMPUTED)}]", f"$params.bad + '{rng.randint(0, 9)}'"])
        contract = _contract({**copy.deepcopy(BASE[op]), key: value})
        assert _refused(contract, key, rng.choice(["zed", "Ann", "x"])), (op, key, value)


def test_a_winner_is_an_entity_a_list_of_them_or_a_side_s_name():
    for winner in ("$actor", "[$actor, $entity(b)]", "'villagers'"):
        result, _ = _call(_contract({"end": "over", "winner": winner}), "")
        assert result.ok, (winner, result.text)
    for winner in ("$round", "$dict(p, $it.id, 1)", "[$round]", "$round > 0"):
        assert _refused(_contract({"end": "over", "winner": winner}), "winner", ""), winner
