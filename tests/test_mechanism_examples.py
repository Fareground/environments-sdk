"""Every mechanism example the reference pages show works as written: with the contract parts the page shows with it
(its mode's `context`) and a type of agents for its `who`, and nothing else, it checks with no error and plays three
rounds.

The reference shows each example under the name the page gives it (``my_<mode>``, or the one name a mode must have).
A new mode whose example does not work fails here until its example, or the context registered with it, does.
"""
import copy
import json

import pytest

import fg_env
import fg_env.mechanisms  # noqa: F401  (registers them)
from fg_env.host.stubs import StubEvaluator, StubGameMaster
from fg_env.registry import FAMILIES

#: The hosts an example consults, bound for its check and run.
ADAPTERS = {"host.judge": {"judge": StubEvaluator}, "host.game_master": {"game_master": StubGameMaster}}


def _merge(into, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict) and key != "clock":
            _merge(into[key], value)
        else:
            into[key] = copy.deepcopy(value)


def _placed(spec):
    """The example of ``spec`` under the name the reference shows it with, in a contract of what it assumes."""
    example = spec.example
    contract = {"name": "Host", "clock": {"rounds": 3}, "types": {}, "entities": {},
                "mechanisms": {spec.name or f"my_{spec.mode}": copy.deepcopy(example)}}
    who = example.get("who")
    for kind in [who] if isinstance(who, str) else who or []:
        contract["types"][kind] = {"agent": True}
        contract["entities"][kind] = {"type": kind, "count": 2}
    _merge(contract, spec.context)
    mechanisms = contract["mechanisms"]  # what the example builds on is declared before it
    name = spec.name or f"my_{spec.mode}"
    contract["mechanisms"] = {**{key: use for key, use in mechanisms.items() if key != name}, name: mechanisms[name]}
    return contract


MODES = [spec for family in FAMILIES.values() for spec in family.modes.values()]


@pytest.mark.parametrize("spec", MODES, ids=[spec.key for spec in MODES])
def test_every_reference_example_checks_clean_and_plays_three_rounds(spec):
    contract = _placed(spec)
    adapters = {name: make() for name, make in ADAPTERS.get(spec.key, {}).items()} or None
    errors = [str(issue) for issue in fg_env.check(contract, rounds=3, hosts=adapters) if issue.severity == "error"]
    assert errors == []
    result = fg_env.run(contract, seed=1, hosts=adapters)
    assert result.status != "failed", result.error


def _whole_fields(example):
    """The fields of a reference example written as whole numbers (not flags)."""
    return [key for key, value in example.items() if isinstance(value, int) and not isinstance(value, bool)]


def _played(contract):
    """The world a run left (entities, world properties, log) as one value, or None when the contract is refused (an
    error at load or in the run)."""
    try:
        env = fg_env.load(contract, seed=1)
    except fg_env.ContractError:
        return None
    if env.run().status == "failed":
        return None
    state = env.state.encode()
    return json.dumps([state["entities"], state["props"], state["log"]], sort_keys=True, default=str)


def _fraction_rounded(spec, key):
    """Whether the example's whole-number field ``key`` given as x + 0.5 plays exactly as x or as x + 1 though those
    two play differently: the fraction was rounded away without a word."""
    name = spec.name or f"my_{spec.mode}"
    value = spec.example[key]
    runs = {}
    for shift in (0, 1, 0.5):
        contract = _placed(spec)
        contract["mechanisms"][name][key] = value + shift
        runs[shift] = _played(contract)
    if None in (runs[0], runs[1]) or runs[0] == runs[1] or runs[0.5] is None:
        return False  # the field does not change this run, or a fraction is refused: nothing was rounded
    return runs[0.5] in (runs[0], runs[1])


HOSTLESS = [spec for spec in MODES if spec.key not in ADAPTERS]


@pytest.mark.parametrize("spec", HOSTLESS, ids=[spec.key for spec in HOSTLESS])
def test_no_mechanism_rounds_a_fraction_away_without_a_word(spec):
    """audit 13 H1: a whole-number field given a fraction is refused at the field (or honoured), never silently
    rounded: a run with x + 0.5 that plays exactly as x or as x + 1 (which play differently) rounded it."""
    fields = _whole_fields(spec.example)
    if not fields:
        pytest.skip("the example has no whole-number field")
    rounded = [key for key in fields if _fraction_rounded(spec, key)]
    assert rounded == [], f"{spec.key}: {rounded} rounded a fraction"


def _number_fields(example):
    return [key for key, value in example.items() if isinstance(value, (int, float)) and not isinstance(value, bool)]


NUMBERED = [spec for spec in HOSTLESS if _number_fields(spec.example)]


@pytest.mark.parametrize("spec", NUMBERED, ids=[spec.key for spec in NUMBERED])
def test_a_literal_that_is_no_number_is_reported_at_the_mechanism_field(spec):
    """audit 13 mechanisms M2: a number field given a word is an error at `mechanisms.<name>.<field>`, where the author
    wrote it, never at a part the mechanism generated from it or only after a smoke run."""
    name = spec.name or f"my_{spec.mode}"
    for key in _number_fields(spec.example):
        contract = _placed(spec)
        contract["mechanisms"][name][key] = "lots"
        errors = [issue for issue in fg_env.check(contract, rounds=0) if issue.severity == "error"]
        assert errors and all(issue.path.startswith(f"mechanisms.{name}") for issue in errors), (key, errors)
