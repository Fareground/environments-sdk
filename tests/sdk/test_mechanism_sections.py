"""Mechanisms extend declared actions through action_hooks, generate other mechanisms, and document nested config."""
from typing import Dict

from pydantic import BaseModel, Field

from fg_env.sdk.guide import guide
from fg_env.sdk.mechanisms import expand_mechanisms
from fg_env.sdk.registry import MECHANISMS, mechanism


class _Nothing(BaseModel):
    pass


class _Rule(BaseModel):
    """One rule of the nested test mechanism."""

    limit: int = Field(3, description="How many times.")


class _WithRules(BaseModel):
    rules: Dict[str, _Rule] = Field(default_factory=dict, description="Rules by name.")


def _register(kind, config, expand):
    if kind not in MECHANISMS:
        mechanism(kind, config, f"Test mechanism {kind}.")(expand)


GUARD = {"when": [{"expr": "$actor.cash > 0", "why": "you are broke"}], "do": ["$actor.cash -= 1"]}
_register("test_guard", _Nothing, lambda name, cfg, contract: {"action_hooks": {"shout": GUARD}})
_register("test_inner", _Nothing, lambda name, cfg, contract: {"world": {f"{name}_ran": 1}})
_register("test_outer", _Nothing, lambda name, cfg, contract: {"mechanisms": {f"{name}_inner": {"kind": "test_inner"}}})
_register("test_forever", _Nothing, lambda name, cfg, contract: {"mechanisms": {f"{name}x": {"kind": "test_forever"}}})
_register("test_rules", _WithRules, lambda name, cfg, contract: {})

SHOUT = {"shout": {"by": "player", "when": "$round > 0", "do": ["$world.noise += 1"]}}


def test_action_hooks_extend_declared_actions_once_and_keep_the_authors_parts():
    data, issues = expand_mechanisms({"actions": SHOUT, "mechanisms": {"a": {"kind": "test_guard"}, "b": {"kind": "test_guard"}}})
    assert issues == []
    shout = data["actions"]["shout"]
    assert shout["when"] == ["$round > 0", GUARD["when"][0]]
    assert shout["do"] == ["$world.noise += 1", "$actor.cash -= 1"]


def test_an_action_hook_on_an_undeclared_action_is_reported_against_the_mechanism():
    _, issues = expand_mechanisms({"actions": {}, "mechanisms": {"a": {"kind": "test_guard"}}})
    assert len(issues) == 1 and "mechanisms.a" in str(issues[0]) and "no action 'shout'" in str(issues[0])


def test_a_mechanism_can_generate_another_mechanism():
    data, issues = expand_mechanisms({"mechanisms": {"outer": {"kind": "test_outer"}}})
    assert issues == []
    assert data["mechanisms"]["outer_inner"] == {"kind": "test_inner"}
    assert data["world"]["outer_inner_ran"] == 1


def test_mechanisms_generating_each_other_without_end_are_reported_not_looped():
    _, issues = expand_mechanisms({"mechanisms": {"loop": {"kind": "test_forever"}}})
    assert any("without end" in str(i) for i in issues)


def test_the_guide_documents_nested_mechanism_config():
    text = guide("mechanisms")
    section = text[text.index("### `test_rules`"):]
    assert "**_Rule**" in section and "`limit`" in section
