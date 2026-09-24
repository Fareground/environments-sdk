"""Earlier mechanism and library forms load as the current ones: folded families, removed modes (refused with what to
write instead), removed functions; and a mechanism's functions exist only beside a mechanism of its family."""
import json

import pytest

import fg_env
from fg_env.contract.normalize import normalize

HEARING = {
    "name": "Hearing", "clock": {"rounds": 3},
    "types": {"lawyer": {"agent": True}},
    "entities": {"pat": {"type": "lawyer"}},
    "actions": {"object": {"by": "lawyer", "do": [{"flow": "trial", "action": "push", "item": "objection"}]}},
    "mechanisms": {"trial": {"kind": "flow", "mode": "procedure", "stack": {"who": "lawyer", "kinds": {
        "objection": {"tool": False, "resolve": ["$world.objections += 1"]}}}}},
    "world": {"objections": 0},
}


def _errors(contract):
    return [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]


def test_a_folded_family_is_renamed_with_the_ops_named_after_it():
    current, notes = normalize(HEARING)
    assert current["mechanisms"]["trial"]["kind"] == "decision"
    assert current["actions"]["object"]["do"] == [{"decision": "trial", "action": "push", "item": "objection"}]
    assert notes == ["mechanisms.trial: kind 'flow' → 'decision' (mode 'procedure')",
                     "actions.object.do[0]: effect op `flow` → `decision`"]
    assert normalize(current) == (current, [])
    assert _errors(HEARING) == []


@pytest.mark.parametrize("kind, mode, hint", [
    ("flow", "victory", "declare `end` entries with a `winner`"),
    ("flow", "order", "set the stage's `order`"),
    ("conditions", "cooldowns", "use a `game.status` mechanism"),
    ("social", "channels", "post to a record"),
    ("mind", "beliefs", "keep each agent's beliefs in props"),
    ("agreements", "labor", "declare jobs yourself"),
    ("game", "slots", "declare each space as an entity"),
])
def test_a_removed_mode_is_refused_with_what_to_write_instead(kind, mode, hint):
    contract = {**HEARING, "mechanisms": {"old": {"kind": kind, "mode": mode}}}
    [issue] = _errors(contract)
    assert issue.path == "mechanisms.old" and issue.message == f"the `{kind}.{mode}` mechanism was removed"
    assert issue.fix.startswith(hint)


def _dice(expr):
    return {"name": "Dice", "clock": {"rounds": 3}, "types": {"p": {"agent": True}},
            "entities": {"a": {"type": "p"}, "b": {"type": "p"}}, "world": {"rolls": {"type": "list", "default": []}},
            "events": [{"do": [f"$world.rolls += [{expr}]"]}], "outputs": {"rolls": "$world.rolls"}}


@pytest.mark.parametrize("old, new", [
    ("$random()", "$uniform(0, 1)"),
    ("$exists('a')", "$get($entity('a'), 'alive', false)"),
    ("$ids(p)", "$map(p, $it.id)"),
    ("$index_of('banana', 'na')", "$index('banana', 'na')"),
    ("$count_text('banana', 'an')", "($len($split('banana', 'an')) - 1)"),
])
def test_a_removed_function_is_rewritten_into_one_that_gives_the_same_result(old, new):
    current, notes = normalize(_dice(old))
    assert current["events"][0]["do"] == [f"$world.rolls += [{new}]"] and len(notes) == 1
    assert fg_env.run(_dice(old), seed=4).outputs == fg_env.run(_dice(new), seed=4).outputs


def test_a_removed_function_the_contract_defines_itself_is_left_alone():
    own = {**_dice("$ids(2)"), "defs": {"ids": {"args": ["n"], "expr": "$n * 10"}}}
    assert normalize(own) == (own, [])
    assert fg_env.run(own, seed=1).outputs["rolls"] == [20, 20, 20]


POT = {"name": "Pot", "clock": {"rounds": 1}, "types": {"player": {"agent": True, "props": {"chips": 100}}},
       "entities": {"a": {"type": "player"}, "b": {"type": "player"}},
       "outputs": {"pot": "$pot_total()"}}


def test_a_mechanism_function_needs_a_mechanism_of_its_family():
    [issue] = _errors(POT)
    assert issue.message == "$pot_total reads a `game` mechanism, and this contract declares none"
    assert issue.fix.startswith("declare one (guide('game'))")
    table = {**POT, "outputs": {"pot": "$pot_total(table)"}, "mechanisms": {"table": {
        "kind": "game", "mode": "pot", "who": "player", "stack": "chips", "blinds": [1, 2], "score": "$it.chips"}}}
    assert _errors(table) == []


@pytest.mark.parametrize("typo, hint", [("$fitler(p, true)", "$filter"), ("$total(p)", "$sum"),
                                        ("$pot_totl()", None)])
def test_an_unknown_function_suggests_a_core_one_first_and_never_an_undeclared_mechanisms(typo, hint):
    issue = next(i for i in _errors({**POT, "outputs": {"x": typo}}) if "unknown function" in i.message)
    if hint is None:
        assert "$pot_total" not in (issue.fix or "")
    else:
        assert issue.fix.startswith(f"did you mean {hint}?"), issue.fix


def test_best_is_a_core_function():
    result = fg_env.run({**POT, "outputs": {"richest": "$best(player, $it.chips, 'all')"}}, seed=1)
    assert result.ok and result.outputs["richest"] == ["a", "b"]


GROWTH = {"name": "Growth", "clock": {"rounds": 4}, "types": {"shop": {"agent": True, "props": {"sales": 0.0}}},
          "entities": {"s": {"type": "shop"}}, "world": {"temp": 0.0, "load": 0.0},
          "physics": {"params": {"k": 0.5}, "vars": {"x": {"start": 1, "rate": "k * x"}}, "write": {"world.load": "x"}},
          "feeds": {"weather": {"host": "weather", "into": "world.temp", "fallback": "$normal(10, 2)"}},
          "patterns": {"trend": {"kind": "trend", "start": 10, "slope": 1},
                       "size": {"kind": "draw", "dist": "triangular", "low": 1, "mode": 2, "high": 5}},
          "events": [{"do": ["$entity(s).sales = $pattern.trend * $pattern.size"]}],
          "outputs": {"x": "$physics.x", "sales": "$entity(s).sales", "temp": "$world.temp"}}


def test_physics_feeds_and_patterns_are_mechanisms_read_the_same_way():
    current, _ = normalize(GROWTH)
    assert not {"physics", "feeds", "patterns"} & set(current)
    assert current["mechanisms"] == {
        "physics": {"kind": "dynamics", "mode": "ode", **GROWTH["physics"]},
        "weather": {"kind": "host", "mode": "feed", **GROWTH["feeds"]["weather"]},
        "trend": {"kind": "pattern", "mode": "trend", "start": 10, "slope": 1},
        "size": {"kind": "pattern", "mode": "draw", "dist": "triangular", "low": 1, "peak": 2, "high": 5}}
    assert normalize(current) == (current, [])
    assert _errors(GROWTH) == []
    assert fg_env.run(GROWTH, seed=3).outputs == fg_env.run(current, seed=3).outputs


def test_a_pattern_named_like_another_mechanism_is_renamed_with_every_reference():
    clash = {**GROWTH, "mechanisms": {"trend": {"kind": "decision", "mode": "ballot", "who": "shop",
                                                "options": ["up", "down"]}},
             "patterns": {**GROWTH["patterns"], "prod": {"kind": "product", "scale": 2, "of": ["trend"]}}}
    current, notes = normalize(clash)
    assert current["mechanisms"]["trend"]["kind"] == "decision"
    assert current["mechanisms"]["trend_pattern"] == {"kind": "pattern", "mode": "trend", "start": 10, "slope": 1}
    assert current["mechanisms"]["prod"]["of"] == ["trend_pattern"]
    assert current["events"][0]["do"] == ["$entity(s).sales = $pattern.trend_pattern * $pattern.size"]
    assert any("renamed 'trend_pattern'" in note for note in notes)
    assert _errors(clash) == []


def test_an_arm_or_fork_patch_in_the_earlier_form_changes_the_mechanism():
    armed = {**GROWTH, "arms": {"steep": {"patch": {"patterns": {"trend": {"slope": 3}},
                                                     "physics": {"params": {"k": 1}}}}}}
    current, _ = normalize(armed)
    assert current["arms"]["steep"]["patch"] == {"mechanisms": {"trend": {"slope": 3},
                                                                "physics": {"kind": "dynamics", "mode": "ode",
                                                                            "params": {"k": 1}}}}
    steep = fg_env.run(armed, seed=1, arm="steep").outputs
    assert steep["sales"] != fg_env.run(armed, seed=1).outputs["sales"]
    env = fg_env.load(GROWTH, seed=1)
    env.run(rounds=1)
    forked = fg_env.fork(GROWTH, env.snapshot(), patch={"patterns": {"trend": {"slope": 3}}})
    assert forked.contract.mechanisms["trend"] == {"kind": "pattern", "mode": "trend", "start": 10, "slope": 3}


def test_the_dynamics_mechanism_is_named_physics():
    renamed = {**{key: value for key, value in GROWTH.items() if key != "physics"},
               "mechanisms": {"motion": {"kind": "dynamics", "mode": "ode", **GROWTH["physics"]}}}
    [issue] = [i for i in _errors(renamed) if i.path.startswith("mechanisms.motion")]
    assert "is named 'physics'" in issue.message and issue.fix == "rename mechanisms.motion to mechanisms.physics"


def test_an_imported_file_in_the_earlier_form_is_normalized_before_it_is_merged(tmp_path):
    (tmp_path / "physics.json").write_text(json.dumps({"physics": GROWTH["physics"]}))
    main = {key: value for key, value in GROWTH.items() if key != "physics"}
    (tmp_path / "main.json").write_text(json.dumps({**main, "imports": ["physics.json"]}))
    contract = fg_env.parse(tmp_path / "main.json")
    assert contract.physics is not None and contract.physics.params == {"k": 0.5}
