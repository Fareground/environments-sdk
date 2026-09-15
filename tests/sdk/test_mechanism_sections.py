"""Mechanisms extend declared actions through action_hooks, generate other mechanisms, and document nested config."""
from typing import Dict

import pytest
from pydantic import BaseModel, ConfigDict, Field

from fg_env.sdk.guide import guide
from fg_env.sdk.mechanisms import expand_mechanisms
from fg_env.sdk.registry import mode

from family_fixtures import Nothing, scratch_family

FAMILY = "test_sections"


class _Rule(BaseModel):
    """One rule of the nested test mechanism."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(3, description="How many times.")


class _WithRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: Dict[str, _Rule] = Field(default_factory=dict, description="Rules by name.")


GUARD = {"when": [{"expr": "$actor.cash > 0", "why": "you are broke"}], "do": ["$actor.cash -= 1"]}
SHOUT = {"shout": {"by": "player", "when": "$round > 0", "do": ["$world.noise += 1"]}}


@pytest.fixture(autouse=True, scope="module")
def sections_family():
    with scratch_family(FAMILY):
        mode(FAMILY, "guard", Nothing, "Guards the shout action.")(lambda name, cfg, contract: {"action_hooks": {"shout": GUARD}})
        mode(FAMILY, "inner", Nothing, "Marks that it ran.")(lambda name, cfg, contract: {"world": {f"{name}_ran": 1}})
        mode(FAMILY, "outer", Nothing, "Generates an inner mechanism.")(
            lambda name, cfg, contract: {"mechanisms": {f"{name}_inner": {"kind": FAMILY, "mode": "inner"}}})
        mode(FAMILY, "forever", Nothing, "Generates itself without end.")(
            lambda name, cfg, contract: {"mechanisms": {f"{name}x": {"kind": FAMILY, "mode": "forever"}}})
        mode(FAMILY, "rules", _WithRules, "Has nested config.")(lambda name, cfg, contract: {})
        yield


def _use(which):
    return {"kind": FAMILY, "mode": which}


def test_action_hooks_extend_declared_actions_once_and_keep_the_authors_parts():
    data, issues = expand_mechanisms({"actions": SHOUT, "mechanisms": {"a": _use("guard"), "b": _use("guard")}})
    assert issues == []
    shout = data["actions"]["shout"]
    assert shout["when"] == ["$round > 0", GUARD["when"][0]]
    assert shout["do"] == ["$world.noise += 1", "$actor.cash -= 1"]


def test_an_action_hook_on_an_undeclared_action_is_reported_against_the_mechanism():
    _, issues = expand_mechanisms({"actions": {}, "mechanisms": {"a": _use("guard")}})
    assert len(issues) == 1 and "mechanisms.a" in str(issues[0]) and "no action 'shout'" in str(issues[0])


def test_a_mechanism_can_generate_another_mechanism():
    data, issues = expand_mechanisms({"mechanisms": {"outer": _use("outer")}})
    assert issues == []
    assert data["mechanisms"]["outer_inner"] == _use("inner")
    assert data["world"]["outer_inner_ran"] == 1


def test_mechanisms_generating_each_other_without_end_are_reported_not_looped():
    _, issues = expand_mechanisms({"mechanisms": {"loop": _use("forever")}})
    assert any("without end" in str(i) for i in issues)


def test_the_guide_documents_nested_mechanism_config_and_nested_typos_name_their_fields():
    section = guide(f"{FAMILY}.rules")
    assert "**_Rule**" in section and "`limit`" in section
    _, issues = expand_mechanisms({"mechanisms": {"r": {**_use("rules"), "rules": {"a": {"limt": 1}}}}})
    assert [(i.path, i.message, i.fix) for i in issues] == [
        ("mechanisms.r.rules.a.limt", "`limt` is not a field of `rules.a`", "did you mean 'limit'? `rules.a` takes: limit")]
