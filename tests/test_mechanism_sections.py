"""Mechanisms extend declared actions through action_hooks, generate other mechanisms, and document nested config."""

import pytest
from family_fixtures import Nothing, scratch_family
from pydantic import BaseModel, ConfigDict, Field

import fg_env
from fg_env.guides import guide
from fg_env.mechanisms import expand_mechanisms
from fg_env.registry import mode

FAMILY = "test_sections"


class _Rule(BaseModel):
    """One rule of the nested test mechanism."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(3, description="How many times.")


class _WithRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: dict[str, _Rule] = Field(default_factory=dict, description="Rules by name.")


GUARD = {"when": [{"expr": "$actor.cash > 0", "why": "you are broke"}], "do": ["$actor.cash -= 1"]}
SHOUT = {"shout": {"by": "player", "when": "$round > 0", "do": ["$world.noise += 1"]}}


@pytest.fixture(autouse=True, scope="module")
def sections_family():
    with scratch_family(FAMILY):
        mode(FAMILY, "guard", Nothing, "Guards the shout "
                                       "action.")(lambda name, cfg, contract: {"action_hooks": {"shout": GUARD}})
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
        ("mechanisms.r.rules.a.limt", "`limt` is not a field of `rules.a`",
         "did you mean 'limit'? `rules.a` takes: limit")]


AUCTION = {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder"}}


@pytest.mark.parametrize("section, value, shape",
                         [("entities", [{"type": "bidder"}], "an object"), ("world", [], "an object"),
                          ("stages", {"bid": {}}, "a list"), ("events", "none", "a list")])
def test_a_malformed_section_is_reported_as_such_before_any_mechanism_expands(section, value, shape):
    contract = {"name": "Sale", "types": {"bidder": {"agent": True}}, section: value, "mechanisms": AUCTION}
    found = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert [i.path for i in found] == [section] and found[0].message.startswith(f"must be {shape}, got"), found
    assert "bug in the mechanism" not in str(found)


def test_declaring_a_world_property_a_mechanism_keeps_is_an_error_naming_the_fix():
    ballot = {"a": {"kind": "decision", "mode": "ballot", "who": "voter", "options": ["yes", "no"]}}
    contract = {"name": "Vote", "types": {"voter": {"agent": True}}, "population": [{"type": "voter", "count": 3}],
                "world": {"a_result": 5}, "mechanisms": ballot}
    found = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert [i.path for i in found] == ["world.a_result"] and "the mechanism 'a' keeps" in found[0].message
    assert "rename" in found[0].fix
    expanded = fg_env.expand({**contract, "world": {}}, mechanisms=True)  # an expanded contract loads again unchanged
    assert [i for i in fg_env.check(expanded) if i.severity == "error"] == []


def test_a_declared_stage_refining_a_generated_one_is_held_only_when_both_whens_hold():
    """The ballot's own `when` (never) must not be dropped by the author's stage of the same name (always)."""
    c = {"name": "r", "clock": {"rounds": 2}, "types": {"v": {"agent": True}},
         "entities": {"v": {"type": "v", "count": 3}},
         "mechanisms": {"poll": {"kind": "decision", "mode": "ballot", "who": "v", "options": ["a", "b"],
                                 "when": "$round == 99"}},
         "stages": [{"name": "poll", "turns": "simultaneous", "when": "$round >= 1"}],
         "outputs": {"r": "$world.poll_result"}}
    expanded, issues = expand_mechanisms(c)
    assert not issues and expanded["stages"][0]["when"] == "($round >= 1) and ($round == 99)"
    result = fg_env.run(c, "random", seed=1)
    assert [e for e in result.events if e["kind"] == "poll"] == []


def test_an_approval_ballot_with_no_options_is_not_offered_and_counts_nothing():
    c = {"name": "r", "clock": {"rounds": 1}, "world": {"opts": {"type": "list", "default": []}},
         "types": {"v": {"agent": True}}, "entities": {"v": {"type": "v", "count": 3}},
         "mechanisms": {"poll": {"kind": "decision", "mode": "ballot", "who": "v", "method": "approval",
                                 "options": "$world.opts"}},
         "outputs": {"r": "$world.poll_result"}}
    offered = []

    def voter(wake):
        offered.extend(tool.name for tool in wake.tools)
        wake.end()

    result = fg_env.run(c, voter, seed=1)
    assert "poll_vote" not in offered
    # a ballot nobody could touch is not counted: no announcement, and no result yet (audit 13 mechanisms M4)
    assert [e["text"] for e in result.events if e["kind"] == "poll"] == [] and result.outputs["r"] == {}
