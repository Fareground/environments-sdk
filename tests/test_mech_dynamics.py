"""Statuses (`game.status`) and procedures (`decision.procedure`)."""
import json
import math
from pathlib import Path

import pytest

import fg_env
from fg_env.errors import ContractError

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"


def _errors(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


def _script(plan):
    """A participant that plays ``plan[(entity_id, round)]`` — a list of (action, args) — then ends its turn."""
    log = []

    def participant(wake):
        for name, args in plan.get((wake.entity_id, wake.round), []):
            result = wake.call(name, args)
            log.append((wake.entity_id, wake.round, name, result.ok, result.text))
            if result.ended:
                return
        wake.end()

    participant.log = log
    return participant


def _tools(env, entity_id):
    return {t["name"] for t in env.preview(entity_id)["tools"]}


def _legal(env, entity_id):
    """Actions the entity may take right now (no preview, so no other agent moves first)."""
    actor = env.world.entities[entity_id]
    return {name for name in env.contract.actions if env.actions.blocked(actor, name, {}, {}) is None}


def _props(env, entity_id):
    return env.entity(entity_id)["props"]


# ---------------------------------------------------------------------------
# game: status
# ---------------------------------------------------------------------------

ARENA = {
    "name": "Arena",
    "clock": {"rounds": 8},
    "types": {"fighter": {"agent": True, "props": {"hp": {"default": 30, "min": 0}, "armor": 2, "speed": 3}}},
    "entities": {"ann": {"type": "fighter"}, "bob": {"type": "fighter"}},
    "actions": {
        "poison": {"by": "fighter", "params": {"target": {"type": "entity", "of": "fighter"}},
                   "do": [{"game": "conditions", "action": "apply", "status": "poison",
                           "who": "$params.target"}]},
        "bash": {"by": "fighter", "params": {"target": {"type": "entity", "of": "fighter"}},
                 "do": [{"game": "conditions", "action": "apply", "status": "stun", "who": "$params.target"}]},
        "guard": {"by": "fighter",
                  "do": [{"game": "conditions", "action": "apply", "status": "shield", "who": "$actor"}]},
        "purge": {"by": "fighter",
                  "do": [{"game": "conditions", "action": "cleanse", "status": "all", "who": "$actor"}]},
        "wait": {"by": "fighter", "do": []},
    },
    "stages": [{"name": "fight", "turns": "sequential", "max_actions": 1}],
    "mechanisms": {"conditions": {"kind": "game", "mode": "status", "who": "fighter", "statuses": {
        "poison": {"duration": 3, "stacking": "refresh", "max_stacks": 3, "tick": ["$it.hp -= 2 * $stacks"],
                   "expire_say": "{$it.name} is no longer poisoned."},
        "stun": {"duration": 1, "blocks": ["poison", "bash", "guard"], "blocked_why": "you are stunned"},
        "shield": {"duration": 2, "modifiers": {"armor": 3, "speed": {"mul": 0.5}}, "immune": ["poison"]},
        "bleed": {"duration": 2, "stacking": "independent", "max_stacks": 4, "tick": ["$it.hp -= $stacks"]},
        "curse": {"duration": None, "unless": "$it.armor > 10", "cleansable": False},
    }}},
    "outputs": {"hp": "$map(fighter, $it.hp)"},
}


def test_status_ticks_scale_with_stacks_and_expire_after_their_duration():
    env = fg_env.load(ARENA, seed=1)
    play = _script({("ann", 1): [("poison", {"target": "bob"})], ("ann", 2): [("poison", {"target": "bob"})]})
    env.run(play, rounds=1)
    assert _props(env, "bob")["hp"] == 30  # applied this round: first tick next round
    assert _props(env, "bob")["conditions"]["poison"]["stacks"] == 1
    env.run(play, rounds=1)  # round 2: tick 1 stack (-2), then a second stack is added
    assert _props(env, "bob")["hp"] == 28 and _props(env, "bob")["conditions"]["poison"]["stacks"] == 2
    env.run(play, rounds=3)  # rounds 3, 4, 5: two stacks tick (-4 each); refreshed to last through round 5
    assert _props(env, "bob")["hp"] == 16
    assert "poison" not in _props(env, "bob")["conditions"]
    assert any("no longer poisoned" in e["text"] for e in env.result().events)


def test_stun_blocks_actions_while_active_and_says_why():
    env = fg_env.load(ARENA, seed=1)
    env.run(_script({("ann", 1): [("bash", {"target": "bob"})]}), rounds=1)
    assert "bash" not in _legal(env, "bob") and "wait" in _legal(env, "bob")
    play = _script({("bob", 2): [("bash", {"target": "ann"})]})
    env.run(play, rounds=1)
    assert play.log[0][3] is False and "you are stunned" in play.log[0][4]
    assert "bash" in _legal(env, "bob")  # expired at the end of round 2


def test_modifiers_immunity_unless_and_cleanse():
    env = fg_env.load(ARENA, seed=1)
    contract = env.contract
    env.run(_script({("ann", 1): [("guard", {})]}), rounds=1)
    world = env.world
    from fg_env.expr import compile_expr
    ann = world.entities["ann"]
    assert compile_expr("$effective($it, 'armor')")(world.scope(it=ann)) == 5
    assert compile_expr("$effective($it, 'speed')")(world.scope(it=ann)) == 1.5
    assert compile_expr("$status_rounds($it, 'shield')")(world.scope(it=ann)) == 2
    play = _script({("bob", 2): [("poison", {"target": "ann"})], ("ann", 3): [("purge", {})]})
    env.run(play, rounds=1)
    assert "poison" not in _props(env, "ann")["conditions"]  # the shield grants immunity
    runner = env.effects
    runner.run([{"game": "conditions", "action": "apply", "status": "curse", "who": "$entity(ann)"}], {}, "test")
    assert "curse" in _props(env, "ann")["conditions"]
    env.run(play, rounds=1)  # ann purges: shield goes, the curse cannot be cleansed
    assert set(_props(env, "ann")["conditions"]) == {"curse"}
    assert contract.mechanisms["conditions"]["mode"] == "status"


def test_independent_stacks_keep_their_own_timers():
    env = fg_env.load(ARENA, seed=1)
    runner = env.effects
    env.run("idle", rounds=1)
    runner.run([{"game": "conditions", "action": "apply", "status": "bleed", "who": "$entity(bob)", "stacks": 2}],
               {}, "test")
    env.run("idle", rounds=1)
    runner.run([{"game": "conditions", "action": "apply", "status": "bleed", "who": "$entity(bob)", "rounds": 3}],
               {}, "test")
    entry = _props(env, "bob")["conditions"]["bleed"]
    assert entry["stacks"] == 3 and len(entry["timers"]) == 3
    env.run("idle", rounds=2)  # round 3: 3 stacks tick and the first two timers end; round 4: 1 stack ticks
    assert _props(env, "bob")["conditions"]["bleed"]["stacks"] == 1
    assert _props(env, "bob")["hp"] == 30 - 2 - 3 - 1


def test_status_config_errors_name_the_path():
    bad = json.loads(json.dumps(ARENA))
    bad["mechanisms"]["conditions"]["statuses"]["stun"]["blocks"] = ["kick"]
    with pytest.raises(ContractError, match="kick"):
        fg_env.parse(bad)
    wrong = json.loads(json.dumps(ARENA))
    wrong["actions"]["wait"]["do"] = [{"game": "conditions", "action": "apply", "status": "posion",
                                       "who": "$actor"}]
    issues = _errors(wrong)
    assert any("posion" in i.message and "did you mean 'poison'" in (i.fix or "") for i in issues)
    typo = json.loads(json.dumps(ARENA))
    typo["mechanisms"]["conditions"]["statuses"]["poison"]["tick"] = ["$it.health -= 1"]
    assert any("health" in i.message for i in _errors(typo))


# ---------------------------------------------------------------------------
# decision: procedure
# ---------------------------------------------------------------------------

HEARING = {
    "name": "Hearing",
    "clock": {"rounds": 20},
    "inputs": {"days": {"type": "int", "default": 2}},
    "world": {"entered": {"type": "list", "default": []}, "exits": 0},
    "types": {"lawyer": {"agent": True, "props": {"spoke": False}}, "clerk": {"agent": True}},
    "entities": {"pat": {"type": "lawyer"}, "dan": {"type": "lawyer"}, "clerk": {"type": "clerk"}},
    "actions": {
        "open": {"by": "lawyer", "do": ["$actor.spoke = true"]},
        "argue": {"by": "lawyer", "do": []},
        "ring": {"by": "clerk", "do": [{"emit": "bell", "say": "The bell rings."}]},
        "rule": {"by": "clerk", "do": [{"end": "ruled", "say": "Ruled."}]},
    },
    "mechanisms": {"hearing": {"kind": "decision", "mode": "procedure", "phases": {
        "opening": {"title": "Openings", "brief": "Give your opening.",
                    "stages": [{"who": "$it.type == lawyer", "actions": ["open"]}],
                    "on_enter": ["$world.entered += opening"], "on_exit": ["$world.exits += 1"],
                    "next": [{"to": "argument", "all_did": "open"}]},
        "argument": {"stages": [{"actions": ["argue"]},
                                {"name": "bell", "actions": ["ring"], "when": "$world.hearing_round == $inputs.days"}],
                     "on_enter": ["$world.entered += argument"],
                     "next": [{"to": "ruling", "event": "bell", "say": "Arguments are closed."},
                              {"to": "ruling", "after": 5}]},
        "ruling": {"stages": [{"actions": ["rule"]}], "terminal": True, "on_enter": ["$world.entered += ruling"]},
    }}},
    "outputs": {"entered": "$world.entered"},
}


def test_procedure_compiles_to_stages_that_follow_the_phase():
    contract = fg_env.parse(HEARING)
    names = [s.name for s in contract.stages]
    assert names == ["hearing_opening", "hearing_argument", "bell", "hearing_ruling"]
    assert "hearing_phase" in contract.stages[0].when and contract.stages[0].brief == "Give your opening."
    assert _errors(HEARING) == []
    env = fg_env.load(HEARING, seed=1)
    assert _tools(env, "pat") >= {"open"} and "argue" not in _tools(env, "pat")


def test_procedure_transitions_by_all_did_event_and_terminal():
    env = fg_env.load(HEARING, seed=1)
    play = _script({("pat", 1): [("open", {})], ("dan", 2): [("open", {})], ("clerk", 4): [("ring", {})]})
    env.run(play, rounds=1)
    assert env.props["hearing_phase"] == "opening"  # dan has not opened yet
    env.run(play, rounds=1)
    assert env.props["hearing_phase"] == "argument" and env.props["exits"] == 1
    assert "argue" in _tools(env, "pat")
    env.run(play, rounds=1)
    assert env.props["hearing_round"] == 1 and env.props["hearing_phase"] == "argument"
    env.run(play, rounds=1)  # round 4: the bell stage runs on day 2 and the clerk rings
    assert env.props["hearing_phase"] == "ruling"
    result = env.run(play, rounds=1)  # the clerk does not rule: the terminal phase ends the run after its round
    assert result.status == "ended" and result.ended_by == "ruling"
    assert result.outputs["entered"] == ["opening", "argument", "ruling"]
    assert any(e.get("text") == "Arguments are closed." for e in result.events)


def test_procedure_errors():
    bad = json.loads(json.dumps(HEARING))
    bad["mechanisms"]["hearing"]["phases"]["opening"]["next"] = "argumnt"
    with pytest.raises(ContractError, match="did you mean 'argument'"):
        fg_env.parse(bad)
    wrong = json.loads(json.dumps(HEARING))
    wrong["mechanisms"]["hearing"]["phases"]["opening"]["next"] = [{"to": "argument", "all_did": "shout"}]
    with pytest.raises(ContractError, match="shout"):
        fg_env.parse(wrong)


# ---------------------------------------------------------------------------
# Spine behaviour shared by every family
# ---------------------------------------------------------------------------

def test_author_entries_win_and_hooks_are_visible_in_the_contract():
    custom = json.loads(json.dumps(ARENA))
    custom["views"] = {"conditions": {"show": "Mine."}}
    contract = fg_env.parse(custom)
    assert contract.views["conditions"].show == "Mine."
    assert any("has_status" in c.expr for c in contract.actions["bash"].when)
    assert contract.types["fighter"].props["hp"].default == 30


def test_a_status_written_as_the_kind_names_its_family():
    old = json.loads(json.dumps(ARENA))
    old["mechanisms"]["conditions"] = {"kind": "status", "on": "fighter", "statuses": {}}
    issue = next(i for i in _errors(old) if i.path == "mechanisms.conditions.kind")
    assert issue.message == "'status' is a mode of kind 'game'"
    renamed = json.loads(json.dumps(ARENA))
    renamed["mechanisms"]["conditions"]["on"] = renamed["mechanisms"]["conditions"].pop("who")
    typo = next(i for i in _errors(renamed) if i.path == "mechanisms.conditions.on")
    assert typo.message == "`on` is not a field of `game` mode `status`" and "who, statuses, phase, views" in typo.fix


def test_status_actions_check_their_own_keys():
    def issues(*effects):
        contract = json.loads(json.dumps(ARENA))
        contract["actions"]["wait"]["do"] = list(effects)
        return [(i.path, i.message, i.fix) for i in _errors(contract)]

    assert issues({"game": "conditions", "action": "apply", "who": "$actor"})[0][1] == "`game.apply` needs `status`"
    cleanse = {"game": "conditions", "action": "cleanse", "status": "all", "who": "$actor", "to": "$actor"}
    assert issues(cleanse)[0][1] == "'to' is not part of `game.cleanse`"
    path, message, fix = issues({"game": "conditions", "action": "clense", "who": "$actor"})[0]
    assert path.endswith(".action") and "is not an action of conditions (game status)" in message
    assert fix == "did you mean 'cleanse'?"
    _, _, fix = issues({"cleanse": "all"})[0]
    assert fix.startswith('`cleanse` is an action of the `game` op: {"game": "<mechanism>", "action": "cleanse"')


def test_guide_documents_the_families_and_their_functions():
    text = fg_env.guide("mechanisms")
    assert "| `game` | board, cards, pot, status |" in text
    assert "| `decision` | ballot, deliberation, procedure |" in text
    status = fg_env.guide("game.status")
    assert "### `game.status`" in status and "- `apply`" in status and '"action": "tick"' not in status
    procedure = fg_env.guide("decision.procedure")
    assert "### `decision.procedure`" in procedure and "- `push`" in procedure
    assert '"action": "advance"' not in procedure
    for name in ("$effective(", "$has_status("):
        assert name in fg_env.guide("game")


# ---------------------------------------------------------------------------
# Acceptance examples
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["civil_trial", "epidemic_shocks"])
def test_acceptance_examples_check_run_and_resume(name):
    path = EXAMPLES / f"{name}.json"
    assert _errors(path) == []
    straight = fg_env.load(path, seed=21).run(rounds=6)
    assert straight.status in ("running", "completed", "ended"), straight.error
    assert fg_env.load(path, seed=21).run(rounds=6).to_dict() == straight.to_dict()
    env = fg_env.load(path, seed=21)
    env.run(rounds=2)
    if not env.finished:
        env = fg_env.Env.restore(path, json.loads(json.dumps(env.snapshot())))
        env.run(rounds=4)
    assert env.result().to_dict() == straight.to_dict()


def test_civil_trial_procedure_reaches_a_verdict():
    procedure = EXAMPLES / "civil_trial.json"
    result = fg_env.load(procedure, seed=2).run()
    assert result.status == "ended", result.error
    assert result.outputs["verdict"] in ("liable", "not_liable", "hung")
    phases = result.outputs["phases"]
    assert phases[:2] == ["opening", "direct"] and "cross" in phases and "deliberation" in phases


def test_epidemic_shocks_draws_its_uncertain_quantities_per_run_and_reports_them():
    path = EXAMPLES / "epidemic_shocks.json"
    outputs = [fg_env.load(path, seed=s, inputs={"residents": 60}).run(rounds=20).outputs for s in range(3)]
    assert len({o["transmissibility"] for o in outputs}) == 3
    assert all(math.isfinite(o["transmissibility"]) for o in outputs)
    assert all("superspreader_events" in o for o in outputs)
