"""The procedure's response stack: items pushed and answered in response windows, resolved last in, first out."""
import json

import pytest

import fg_env
from fg_env.sdk.errors import ContractError
from fg_env.sdk.expr import compile_expr


class Script:
    """Each agent works through its queue: a step runs when its tool is offered; otherwise the turn ends."""

    def __init__(self, queues):
        self.queues = {agent: list(steps) for agent, steps in queues.items()}
        self.results = []
        self.offered = {}

    def __call__(self, wake):
        tools = {tool.name for tool in wake.tools}
        self.offered.setdefault(wake.entity_id, []).append((wake.stage, tools))
        queue = self.queues.get(wake.entity_id) or []
        if queue and queue[0][0] in tools:
            tool, args = queue.pop(0)
            result = wake.call(tool, args)
            self.results.append((wake.entity_id, tool, result.ok, result.text))
        if not wake.done:
            wake.end()


def _props(env, entity_id):
    return env.world.entities[entity_id].properties


def _read(env, source):
    return compile_expr(source)(env.world.scope())


DUEL = {
    "name": "Duel",
    "clock": {"rounds": 3},
    "types": {"wizard": {"agent": True, "props": {"hp": 20, "mana": 3}}},
    "entities": {"ann": {"type": "wizard", "name": "Ann"}, "bob": {"type": "wizard", "name": "Bob"}},
    "stages": [{"name": "main", "actions": ["spells_bolt"]}],
    "mechanisms": {"spells": {"kind": "flow", "mode": "procedure", "stack": {"who": "wizard", "kinds": {
        "bolt": {"params": {"target": {"type": "entity", "of": "wizard", "where": "$it.id != $actor.id"}},
                 "when": "$actor.mana >= 1", "why": "You need 1 mana.", "on_push": ["$actor.mana -= 1"],
                 "resolve": ["$params.target.hp -= 3"], "show": "at {$params.target.name}"},
        "counterspell": {"starts": False, "on": ["bolt", "counterspell"], "when": "$actor.mana >= 2", "why": "You need 2 mana.",
                         "on_push": ["$actor.mana -= 2"], "resolve": [{"flow": "spells", "action": "counter"}]}}}}},
    "outputs": {"hp": "$map(wizard, $it.hp)"},
}


def _acts(env):
    return [(e.data["act"], e.data["item"]) for e in env.world.log if e.kind == "spells_stack"]


def test_counterspells_resolve_last_in_first_out_and_a_countered_counter_lets_the_bolt_through():
    assert [i for i in fg_env.check(DUEL) if i.severity == "error"] == []
    env = fg_env.load(DUEL, seed=1)
    script = Script({
        "ann": [("spells_bolt", {"target": "bob"}), ("spells_counterspell", {})],
        "bob": [("spells_counterspell", {}), ("spells_pass", {}), ("spells_pass", {})],
    })
    result = env.run(script, rounds=1)
    assert result.status == "running", result.error
    assert all(ok for _, _, ok, _ in script.results), script.results
    assert _acts(env) == [("push", 1), ("push", 2), ("push", 3), ("pass", 3), ("resolve", 3), ("counter", 2),
                          ("reopen", 1), ("pass", 1), ("resolve", 1)]
    assert _props(env, "bob")["hp"] == 17 and _props(env, "ann")["hp"] == 20
    assert (_props(env, "ann")["mana"], _props(env, "bob")["mana"]) == (0, 1)
    assert _read(env, "$stack(spells)") == [] and _read(env, "$stack(spells, top)") is None
    news = [e.text for e in env.world.log if e.kind == "spells_stack"]
    assert news[0] == "Ann pushes bolt [1]: at Bob. Waiting on Bob to answer."
    assert "The counterspell [2] by Bob is countered." in news


def test_only_those_who_owe_an_answer_are_woken_and_the_view_says_what_they_may_do():
    env = fg_env.load(DUEL, seed=1)
    seen = {}

    def play(wake):
        seen.setdefault((wake.entity_id, wake.stage), {tool.name for tool in wake.tools})
        if wake.entity_id == "ann" and wake.stage == "main":
            wake.call("spells_bolt", {"target": "bob"})
        if wake.entity_id == "bob" and wake.stage == "spells_stack":
            seen["bob_update"] = wake.update
            seen["waiting"] = _read(env, "$stack(spells, waiting)")
            seen["can_counter"] = _read(env, "[$stack(spells, can_push, counterspell, bob), $stack(spells, can_push, counterspell, ann)]")
        if not wake.done:
            wake.end()

    env.run(play, rounds=1)
    assert "spells_counterspell" not in seen[("ann", "main")]  # nothing to answer, and a counter cannot start a stack
    assert ("ann", "spells_stack") not in seen
    assert {"spells_counterspell", "spells_pass"} <= seen[("bob", "spells_stack")]
    assert "Top: bolt [1] by Ann: at Bob" in seen["bob_update"]
    assert "You may answer with counterspell or pass." in seen["bob_update"]
    assert seen["waiting"] == ["bob"] and seen["can_counter"] == [True, False]
    assert _props(env, "bob")["hp"] == 17  # Bob's silence passed, so the bolt resolved


def test_waiting_windows_carry_the_stack_across_rounds_and_snapshots():
    contract = json.loads(json.dumps(DUEL))
    contract["mechanisms"]["spells"]["stack"].update(silence="wait", unanswered="wait")
    env = fg_env.load(contract, seed=1)
    env.run(Script({"ann": [("spells_bolt", {"target": "bob"})]}), rounds=1)
    top = _read(env, "$stack(spells, top)")
    assert top["kind"] == "bolt" and top["waiting"] == ["bob"] and top["params"]["target"].id == "bob"
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    restored.run(Script({"bob": [("spells_pass", {})]}), rounds=1)
    assert _props(restored, "bob")["hp"] == 17 and _read(restored, "$stack(spells, top)") is None


def test_a_refused_push_rolls_the_action_back_and_says_why():
    contract = json.loads(json.dumps(DUEL))
    contract["actions"] = {"force_counter": {"by": "wizard", "do": [{"flow": "spells", "action": "push", "item": "counterspell"}]}}
    contract["stages"] = [{"name": "main", "actions": ["force_counter"]}]
    env = fg_env.load(contract, seed=1)
    script = Script({"ann": [("force_counter", {})]})
    env.run(script, rounds=1)
    assert script.results[0][2] is False and "nothing is on the stack" in script.results[0][3]
    assert _props(env, "ann")["mana"] == 3


COURT = {
    "name": "Objection",
    "clock": {"rounds": 6},
    "world": {"admitted": {"type": "list", "default": []}, "excluded": {"type": "list", "default": []}, "sustained": False},
    "types": {"attorney": {"agent": True}, "judge": {"agent": True}},
    "entities": {"pat": {"type": "attorney", "name": "Pat"}, "dana": {"type": "attorney", "name": "Dana"},
                 "ito": {"type": "judge", "name": "Judge Ito"}},
    "actions": {"offer": {"by": "attorney", "params": {"exhibit": {"type": "text", "max_len": 40}},
                          "do": [{"flow": "trial", "action": "push", "item": "exhibit", "params": {"name": "$params.exhibit"}}],
                          "terminal": True}},
    "mechanisms": {"trial": {
        "kind": "flow",
        "mode": "procedure",
        "phases": {
            "evidence": {"stages": [{"name": "direct", "actions": ["offer"], "who": "$is($it, attorney)"}],
                         "next": [{"to": "verdict", "when": "$len($world.admitted) + $len($world.excluded) >= 2 and $stack(trial, top) == null"}]},
            "verdict": {"terminal": True}},
        "stack": {"who": ["attorney", "judge"], "reopen": False, "kinds": {
            "exhibit": {"tool": False, "params": {"name": "text"}, "show": "{$params.name}",
                        "responders": "$is($it, attorney) and $it.id != $item.by",
                        "resolve": ["$world.admitted += $params.name"], "countered": ["$world.excluded += $params.name"]},
            "objection": {"starts": False, "on": ["exhibit"], "who": "attorney",
                          "params": {"ground": {"type": "enum", "values": ["hearsay", "relevance"]}},
                          "responders": "$is($it, judge)",
                          "resolve": [{"if": "$world.sustained", "then": [{"flow": "trial", "action": "counter"}]},
                                      "$world.sustained = false"]},
            "ruling": {"starts": False, "on": ["objection"], "who": "judge", "responders": "false",
                       "params": {"decision": {"type": "enum", "values": ["sustained", "overruled"]}},
                       "resolve": ["$world.sustained = $params.decision == sustained"]}}}}},
    "outputs": {"admitted": "$world.admitted", "excluded": "$world.excluded", "phases": "$world.trial_history"},
}


def test_courtroom_objection_is_ruled_on_before_the_exhibit_and_a_sustained_one_excludes_it():
    assert [i for i in fg_env.check(COURT) if i.severity == "error"] == []
    env = fg_env.load(COURT, seed=1)
    script = Script({
        "pat": [("offer", {"exhibit": "Invoice"}), ("offer", {"exhibit": "Photo"})],
        "dana": [("trial_objection", {"ground": "hearsay"}), ("trial_pass", {})],
        "ito": [("trial_ruling", {"decision": "sustained"})],
    })
    result = env.run(script)
    assert result.status == "ended", result.error
    assert all(ok for _, _, ok, _ in script.results), script.results
    assert result.outputs == {"admitted": ["Photo"], "excluded": ["Invoice"], "phases": ["evidence", "verdict"]}
    assert "trial_objection" not in {tool for _, tools in script.offered["ito"] for tool in tools}  # only attorneys object
    first_day = [e.text for e in env.world.log if e.kind == "trial_stack" and e.round == 1]
    assert [t.split(" [")[0] for t in first_day] == [
        "Pat pushes exhibit", "Dana pushes objection", "Judge Ito pushes ruling", "The ruling", "The objection",
        "The exhibit"]
    assert first_day[-1].endswith("is countered.")


def test_an_overruled_objection_lets_the_exhibit_in():
    result = fg_env.run(COURT, Script({
        "pat": [("offer", {"exhibit": "Invoice"}), ("offer", {"exhibit": "Photo"})],
        "dana": [("trial_objection", {"ground": "relevance"})],
        "ito": [("trial_ruling", {"decision": "overruled"})],
    }), seed=1)
    assert result.outputs["admitted"] == ["Invoice", "Photo"] and result.outputs["excluded"] == []


def test_one_examination_stage_runs_many_offers_each_answered_before_it_takes_effect():
    exam = json.loads(json.dumps(COURT))
    exam["clock"]["rounds"] = 1
    exam["world"]["offers"] = 0
    exam["actions"]["offer"]["when"] = ["$actor.id == pat"]
    exam["actions"]["offer"]["do"].insert(0, "$world.offers += 1")
    exam["mechanisms"]["trial"]["stack"]["stage"] = "exam"
    exam["mechanisms"]["trial"]["phases"] = {"evidence": {"stages": [{
        "name": "exam", "actions": ["offer"], "passes": 20,
        "who": "$it.id == pat and $stack(trial, top) == null or $stack(trial, waiting, $it)",
        "until": "$world.offers >= 3 and $stack(trial, top) == null"}]}}
    assert [i for i in fg_env.check(exam) if i.severity == "error"] == []
    seen = []

    def play(wake):  # E1 is objected to and excluded, E2 goes unopposed, E3 is objected to and admitted
        offered = _read(env, "$world.offers")
        seen.append((wake.entity_id, list(_read(env, "$world.admitted")), list(_read(env, "$world.excluded"))))
        tools = {tool.name for tool in wake.tools}
        if "offer" in tools:
            wake.call("offer", {"exhibit": f"E{offered + 1}"})
        elif "trial_objection" in tools and offered != 2:
            wake.call("trial_objection", {"ground": "hearsay"})
        elif "trial_ruling" in tools:
            wake.call("trial_ruling", {"decision": "sustained" if offered == 1 else "overruled"})
        if not wake.done:
            wake.end()

    env = fg_env.load(exam, seed=1)
    result = env.run(play)
    assert result.status == "completed", result.error
    assert result.outputs["admitted"] == ["E2", "E3"] and result.outputs["excluded"] == ["E1"]
    assert [who for who, _, _ in seen] == ["pat", "dana", "ito", "pat", "dana", "pat", "dana", "ito"]
    assert seen[3][1:] == ([], ["E1"]) and seen[5][1:] == (["E2"], ["E1"])  # each offer settled before the next


def test_stack_runs_with_random_agents_resume_exactly_from_a_snapshot():
    contract = {**DUEL, "clock": {"rounds": 6}}
    straight = fg_env.load(contract, seed=4).run().to_dict()
    env = fg_env.load(contract, seed=4)
    env.run(rounds=2)
    env = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    env.run(rounds=4)
    assert env.result().to_dict() == straight


@pytest.mark.parametrize("stack, message", [
    ({"kinds": {"bolt": {"on": ["fireball"]}}}, "'fireball' in `on` is not a kind"),
    ({"kinds": {"counter": {"starts": False}}}, "no kind `starts` a stack"),
    ({"who": "sorcerer"}, "stack.who 'sorcerer' is not a declared type"),
    ({"kinds": {"bolt": {"responders": "$actor.hp > 0"}}}, "$actor is not available here"),
    ({"stage": "nowhere"}, "no stage 'nowhere'"),
])
def test_stack_config_errors_say_what_to_fix(stack, message):
    contract = json.loads(json.dumps(DUEL))
    contract["mechanisms"]["spells"] = {"kind": "flow", "mode": "procedure", "stack": {"who": "wizard", "kinds": {"bolt": {}}, **stack}}
    with pytest.raises(ContractError) as excinfo:
        fg_env.load(contract)
    assert message in str(excinfo.value)


def test_procedure_steps_are_checked_against_what_the_procedure_declares():
    contract = json.loads(json.dumps(DUEL))
    contract["actions"] = {"cast": {"by": "wizard", "do": [{"flow": "spells", "action": "push", "item": "fireball"},
                                                           {"flow": "spells", "action": "advance"}]}}
    issues = {(i.path, i.message) for i in fg_env.check(contract) if i.severity == "error"}
    assert ("actions.cast.do[0].item", "'fireball' is not a kind of the spells stack") in issues
    assert ("actions.cast.do[1].action", "the spells procedure has no phases") in issues
    with pytest.raises(ContractError, match="a procedure needs phases, a stack, or both"):
        fg_env.load({**DUEL, "mechanisms": {"spells": {"kind": "flow", "mode": "procedure"}}})


def test_an_old_procedure_kind_or_field_names_the_new_one():
    old = json.loads(json.dumps(DUEL))
    old["mechanisms"]["spells"] = {"kind": "procedure", "stack": {"players": "wizard", "kinds": {"bolt": {}}}}
    issue = next(i for i in fg_env.check(old) if i.path == "mechanisms.spells.kind")
    assert issue.message == "'procedure' is now kind 'flow' with mode 'procedure'"
    typo = json.loads(json.dumps(DUEL))
    typo["mechanisms"]["spells"]["stack"]["players"] = typo["mechanisms"]["spells"]["stack"].pop("who")
    issue = next(i for i in fg_env.check(typo) if i.path == "mechanisms.spells.stack.players")
    assert issue.message == "`players` is not a field of `stack`" and "who, kinds" in issue.fix


def test_procedure_actions_check_their_own_keys():
    def issues(*effects):
        contract = json.loads(json.dumps(DUEL))
        contract["actions"] = {"cast": {"by": "wizard", "do": list(effects)}}
        contract["stages"] = [{"name": "main", "actions": ["cast"]}]
        return [(i.path, i.message, i.fix) for i in fg_env.check(contract) if i.severity == "error"]

    assert issues({"flow": "spells", "action": "push"})[0][1] == "`flow.push` needs `item`"
    assert issues({"flow": "spells", "action": "pass", "kind": "bolt"})[0][1] == "'kind' is not part of `flow.pass`"
    path, _, fix = issues({"flow": "spells", "action": "psh", "item": "bolt"})[0]
    assert path.endswith(".action") and fix == "did you mean 'push'?"
    _, _, fix = issues({"procedure": "spells", "step": "pass"})[0]
    assert fix.startswith('`procedure` is now the `flow` op: {"flow": "<mechanism>", "action": <action>')


def test_tools_one_offers_the_stack_as_one_tool():
    contract = json.loads(json.dumps(DUEL))
    contract["mechanisms"]["spells"]["tools"] = "one"
    env = fg_env.load(contract, seed=1)
    offered = {}

    def play(wake):
        tools = {tool.name: tool for tool in wake.tools}
        offered.setdefault((wake.entity_id, wake.stage), tools)
        if wake.entity_id == "ann" and wake.stage == "main":
            assert wake.call("spells", {"action": "bolt", "target": "bob"}).ok
        if not wake.done:
            wake.end()

    env.run(play, rounds=1)
    main = offered[("ann", "main")]
    assert "spells" in main and "spells_bolt" not in main
    assert "bolt" in main["spells"].input_schema["properties"]["action"]["enum"]
    assert _props(env, "bob")["hp"] == 17  # Bob's silence passed, so the bolt resolved
