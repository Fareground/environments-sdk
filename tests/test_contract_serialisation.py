"""Contracts serialise cleanly: numbers stay numbers and Pydantic never warns about a value it did not expect."""
import importlib
import inspect
import pkgutil
import typing
import warnings
from pathlib import Path

import pytest
from pydantic import BaseModel
from test_examples import example_params

import fg_env
from fg_env.api import contract_source, parse

EXAMPLES = sorted((Path(__file__).parents[1] / "examples" / "contracts").glob("*.json"))


def _models():
    seen = set()
    for info in pkgutil.walk_packages(fg_env.__path__, "fg_env."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if (inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__.startswith("fg_env")
                and obj not in seen):
                seen.add(obj)
                yield obj


def _flat(annotation):
    out = set()
    for arg in typing.get_args(annotation) or (annotation,):
        out |= set(typing.get_args(arg) or (arg,))
    return out


def test_no_number_or_expression_field_defaults_to_a_value_its_serialiser_does_not_expect():
    wrong = []
    for model in _models():
        for name, field in model.model_fields.items():
            default = field.default
            if isinstance(default, int) and not isinstance(default, bool):
                kinds = _flat(field.annotation)
                if float in kinds and int not in kinds and len(kinds) > 1:
                    wrong.append(f"{model.__module__}.{model.__name__}.{name} = {default!r}")
    assert wrong == []


@pytest.mark.parametrize("path", example_params(EXAMPLES))
def test_every_shipped_contract_serialises_without_pydantic_warnings(path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        contract = parse(path)
        source = contract_source(contract)
        contract.model_dump(by_alias=True)
        contract.model_dump(by_alias=True, exclude_defaults=True)
    assert contract_source(parse(source)) == source  # a dump parses back to the same contract


def test_a_population_mix_weight_round_trips_as_a_number():
    contract = {"fg_env": "1", "name": "mix", "types": {"person": {"agent": True, "props": {"archetype": ""}}},
                "population": [{"type": "person", "count": 4, "mix": [{"name": "a", "weight": 3}, {"name": "b"}]}]}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = contract_source(fg_env.parse(contract))
        full = fg_env.parse(contract).model_dump(by_alias=True)
    assert dumped["population"][0]["mix"] == [{"name": "a", "weight": 3.0}, {"name": "b"}]
    assert full["population"][0]["mix"][1]["weight"] == 1.0
