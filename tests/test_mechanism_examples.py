"""Every mechanism example the reference pages show works as written: with the contract parts the page shows with it
(its mode's `context`) and a type of agents for its `who`, and nothing else, it checks with no error and plays three
rounds.

The reference shows each example under the name the page gives it (``my_<mode>``, or the one name a mode must have).
A new mode whose example does not work fails here until its example, or the context registered with it, does.
"""
import copy

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
