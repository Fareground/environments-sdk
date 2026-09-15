"""Native mechanism families: statuses, cooldowns, channeling, drift, shocks, priors, procedures,
turn order, victory and terrain."""
import json
import math
from pathlib import Path

import pytest

import fg_env
from fg_env.sdk.errors import ContractError

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"


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
# status_effects
# ---------------------------------------------------------------------------

ARENA = {
    "name": "Arena",
    "clock": {"rounds": 8},
    "types": {"fighter": {"agent": True, "props": {"hp": {"default": 30, "min": 0}, "armor": 2, "speed": 3}}},
    "entities": {"ann": {"type": "fighter"}, "bob": {"type": "fighter"}},
    "actions": {
        "poison": {"by": "fighter", "params": {"target": {"type": "entity", "of": "fighter"}},
                   "do": [{"apply": "poison", "to": "$params.target"}]},
        "bash": {"by": "fighter", "params": {"target": {"type": "entity", "of": "fighter"}},
                 "do": [{"apply": "stun", "to": "$params.target"}]},
        "guard": {"by": "fighter", "do": [{"apply": "shield", "to": "$actor"}]},
        "purge": {"by": "fighter", "do": [{"cleanse": "all", "from": "$actor"}]},
        "wait": {"by": "fighter", "do": []},
    },
    "stages": [{"name": "fight", "turns": "sequential", "max_actions": 1}],
    "mechanisms": {"conditions": {"kind": "status_effects", "on": "fighter", "statuses": {
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
    from fg_env.sdk.expr import compile_expr
    ann = world.entities["ann"]
    assert compile_expr("$effective($it, 'armor')")(world.scope(it=ann)) == 5
    assert compile_expr("$effective($it, 'speed')")(world.scope(it=ann)) == 1.5
    assert compile_expr("$status_rounds($it, 'shield')")(world.scope(it=ann)) == 2
    play = _script({("bob", 2): [("poison", {"target": "ann"})], ("ann", 3): [("purge", {})]})
    env.run(play, rounds=1)
    assert "poison" not in _props(env, "ann")["conditions"]  # the shield grants immunity
    runner = env.effects
    runner.run([{"apply": "curse", "to": "$entity(ann)"}], {}, "test")
    assert "curse" in _props(env, "ann")["conditions"]
    env.run(play, rounds=1)  # ann purges: shield goes, the curse cannot be cleansed
    assert set(_props(env, "ann")["conditions"]) == {"curse"}
    assert contract.mechanisms["conditions"]["kind"] == "status_effects"


def test_independent_stacks_keep_their_own_timers():
    env = fg_env.load(ARENA, seed=1)
    runner = env.effects
    env.run("idle", rounds=1)
    runner.run([{"apply": "bleed", "to": "$entity(bob)", "stacks": 2}], {}, "test")
    env.run("idle", rounds=1)
    runner.run([{"apply": "bleed", "to": "$entity(bob)", "rounds": 3}], {}, "test")
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
    wrong["actions"]["wait"]["do"] = [{"apply": "posion", "to": "$actor"}]
    issues = _errors(wrong)
    assert any("posion" in i.message and "did you mean 'poison'" in (i.fix or "") for i in issues)
    typo = json.loads(json.dumps(ARENA))
    typo["mechanisms"]["conditions"]["statuses"]["poison"]["tick"] = ["$it.health -= 1"]
    assert any("health" in i.message for i in _errors(typo))


# ---------------------------------------------------------------------------
# cooldowns and channeling
# ---------------------------------------------------------------------------

MAGES = {
    "name": "Mages",
    "clock": {"rounds": 10},
    "types": {"mage": {"agent": True, "props": {"hp": 50, "mana": 10, "stunned": False}}},
    "entities": {"ann": {"type": "mage"}, "bob": {"type": "mage"}},
    "actions": {
        "zap": {"by": "mage", "params": {"target": {"type": "entity", "of": "mage"}}, "do": ["$params.target.hp -= 3"]},
        "heal": {"by": "mage", "do": ["$actor.hp += 5"]},
        "meteor": {"by": "mage", "params": {"target": {"type": "entity", "of": "mage"}, "power": {"type": "int", "min": 1, "max": 9}},
                   "do": ["$actor.mana -= 2"]},
        "wait": {"by": "mage", "do": []},
    },
    "stages": [{"name": "act", "turns": "sequential", "max_actions": 3}],
    "mechanisms": {
        "abilities": {"kind": "cooldowns", "actions": {"zap": {"cooldown": 2}, "heal": {"charges": 2, "recharge": 3}}},
        "spells": {"kind": "channeling", "busy": ["zap", "heal"], "actions": {"meteor": {
            "rounds": 2, "resolve": ["$params.target.hp -= 10 * $params.power"], "interrupt": "$actor.stunned",
            "say": "{$actor.name}'s meteor lands.", "interrupt_say": "{$actor.name} loses the spell."}}},
    },
    "outputs": {"hp": "$map(mage, $it.hp)"},
}


def test_cooldown_blocks_until_ready():
    env = fg_env.load(MAGES, seed=1)
    play = _script({("ann", 1): [("zap", {"target": "bob"}), ("zap", {"target": "bob"})],
                    ("ann", 4): [("zap", {"target": "bob"})]})
    env.run(play, rounds=1)
    assert [entry[3] for entry in play.log] == [True, False]
    assert "not ready" in play.log[1][4]
    env.run(play, rounds=1)
    assert "zap" not in _legal(env, "ann")  # round 3 still cooling down
    env.run(play, rounds=1)
    assert "zap" not in _legal(env, "ann")
    env.run(play, rounds=1)
    assert play.log[-1][3] is True and _props(env, "bob")["hp"] == 44


def test_charges_regenerate_lazily():
    env = fg_env.load(MAGES, seed=1)
    world = env.world
    from fg_env.sdk.expr import compile_expr
    charges = compile_expr("$charges($entity(ann), 'heal')")
    play = _script({("ann", 1): [("heal", {}), ("heal", {}), ("heal", {})]})
    env.run(play, rounds=1)
    assert [entry[3] for entry in play.log] == [True, True, False]
    assert charges(world.scope()) == 0
    env.run("idle", rounds=3)  # round 4: one charge back
    assert charges(world.scope()) == 1
    env.run("idle", rounds=3)  # round 7: full again
    assert charges(world.scope()) == 2
    env.effects.run([{"start_cooldown": "heal", "for": "$entity(ann)"}, {"reset_cooldown": "all", "for": "$entity(ann)"}], {}, "t")
    assert charges(world.scope()) == 2


def test_channel_resolves_with_the_original_params_and_keeps_the_caster_busy():
    env = fg_env.load(MAGES, seed=1)
    play = _script({("ann", 1): [("meteor", {"target": "bob", "power": 2})]})
    env.run(play, rounds=1)
    assert _props(env, "ann")["mana"] == 8 and _props(env, "ann")["spells"]["action"] == "meteor"
    assert not {"zap", "heal", "meteor"} & _legal(env, "ann")
    env.run("idle", rounds=1)
    assert _props(env, "bob")["hp"] == 50
    env.run("idle", rounds=1)  # resolves at the start of round 3
    assert _props(env, "bob")["hp"] == 30 and _props(env, "ann")["spells"] == {}
    assert any("meteor lands" in e["text"] for e in env.result().events)
    assert "zap" in _legal(env, "ann")


def test_channel_interrupt_and_fizzle():
    env = fg_env.load(MAGES, seed=1)
    env.run(_script({("ann", 1): [("meteor", {"target": "bob", "power": 1})]}), rounds=1)
    env.effects.run(["$entity(ann).stunned = true"], {}, "t")
    env.run("idle", rounds=2)
    assert _props(env, "bob")["hp"] == 50 and _props(env, "ann")["spells"] == {}
    assert any("loses the spell" in e["text"] for e in env.result().events)

    fizzle = json.loads(json.dumps(MAGES))
    fizzle["mechanisms"]["spells"]["actions"]["meteor"]["resolve"] = ["$params.target.hp -= 10", {"fail": "The sky is clear."}]
    env = fg_env.load(fizzle, seed=1)
    env.run(_script({("ann", 1): [("meteor", {"target": "bob", "power": 1})]}), rounds=3)
    assert _props(env, "bob")["hp"] == 50  # every change of a fizzled spell is rolled back
    assert any("fizzled" in e.get("text", "") for e in env.result().events)


def test_abilities_snapshot_and_resume_identically():
    play = _script({("ann", 1): [("meteor", {"target": "bob", "power": 3})], ("bob", 1): [("heal", {}), ("zap", {"target": "ann"})]})
    straight = fg_env.load(MAGES, seed=4).run(play, rounds=5).to_dict()
    env = fg_env.load(MAGES, seed=4)
    env.run(play, rounds=1)
    restored = fg_env.Env.restore(MAGES, json.loads(json.dumps(env.snapshot())))
    assert restored.run(play, rounds=4).to_dict() == straight


# ---------------------------------------------------------------------------
# drift, shocks, priors
# ---------------------------------------------------------------------------

MARKET = {
    "name": "Drifting market",
    "clock": {"rounds": 40},
    "world": {"price": 100.0, "level": {"default": 0, "min": -5, "max": 5}, "wave": 10.0, "noise": 0.0, "stock": {"type": "int", "default": 50}},
    "types": {"trader": {"agent": True, "props": {"mood": 0.0, "vip": False}}},
    "population": [{"type": "trader", "count": 4, "props": {"vip": "$i == 1"}}],
    "actions": {"wait": {"by": "trader", "do": []}},
    "mechanisms": {"weather": {"kind": "drift", "rules": {
        "climb": {"target": "world.level", "model": "linear", "rate": 0.5},
        "revert": {"target": "world.price", "model": "mean_reversion", "rate": 0.3, "mean": 50},
        "wave": {"target": "world.wave", "model": "sinusoidal", "amplitude": 4, "period": 8},
        "walk": {"target": "world.noise", "model": "random_walk", "sd": 1, "min": -3, "max": 3},
        "shrink": {"target": "world.stock", "model": "linear", "rate": -1.4},
        "cheer": {"target": "trader.mood", "model": "linear", "rate": 0.1, "where": "$it.vip", "when": "$round > 2"},
    }}},
    "outputs": {"price": "$world.price"},
}


def test_drift_models_move_props_every_round():
    env = fg_env.load(MARKET, seed=1)
    env.run("idle", rounds=8)
    props = env.props
    assert props["level"] == 4 and props["price"] == pytest.approx(50 + 50 * 0.7 ** 8)
    assert props["wave"] == pytest.approx(10.0, abs=1e-9)  # a full period returns to the start
    assert -3 <= props["noise"] <= 3 and props["noise"] != 0
    assert props["stock"] == 42  # an int prop moves in whole steps: -1.4 rounds to -1 each round
    moods = [e["props"]["mood"] for e in env.entities("trader")]
    assert moods[0] == pytest.approx(0.6) and moods[1:] == [0, 0, 0]
    env.run("idle", rounds=10)
    assert env.props["level"] == 5  # the prop's own max bounds it


def test_random_drift_is_seeded():
    values = []
    for seed in (1, 1, 2):
        env = fg_env.load(MARKET, seed=seed)
        env.run("idle", rounds=5)
        values.append(env.props["noise"])
    assert values[0] == values[1] != values[2]


OUTBREAK = {
    "name": "Shocks",
    "clock": {"rounds": 30},
    "world": {"cases": 0, "alarm": False, "closed": False, "bonus": 0},
    "types": {"mayor": {"agent": True}},
    "entities": {"mayor": {"type": "mayor"}},
    "actions": {"wait": {"by": "mayor", "do": []}},
    "arms": {"calm": {"patch": {"world": {"bonus": 1}}}},
    "mechanisms": {"shocks": {"kind": "shocks", "shocks": {
        "arrival": {"at": 2, "do": ["$world.cases += 10"], "say": "Ten cases arrive.",
                    "then": [{"shock": "alarm", "after": 2}]},
        "alarm": {"do": ["$world.alarm = true"], "then": [{"shock": "closure", "after": 0, "chance": 1, "when": "$world.cases > 5"}]},
        "closure": {"do": ["$world.closed = true"], "lasts": 3, "undo": ["$world.closed = false"], "end_say": "Reopened."},
        "spreader": {"chance": 0.5, "window": [5, 25], "when": "not $world.closed", "gap": 2, "limit": 4,
                     "do": ["$world.cases += 1"]},
    }}},
    "outputs": {"cases": "$world.cases", "spreaders": "$world.shocks.spreader.count"},
}


def test_shocks_fire_on_schedule_with_cascades_and_durations():
    env = fg_env.load(OUTBREAK, seed=3)
    env.run("idle", rounds=4)
    state = env.props["shocks"]
    assert state["arrival"]["rounds"] == [2] and state["alarm"]["rounds"] == [4] and state["closure"]["rounds"] == [4]
    assert env.props["closed"] is True
    env.run("idle", rounds=3)
    assert env.props["closed"] is False
    assert any(e.get("text") == "Reopened." for e in env.result().events)
    env.run("idle")
    spreads = env.props["shocks"]["spreader"]["rounds"]
    assert 0 < len(spreads) <= 4 and all(5 <= r <= 25 for r in spreads)
    assert all(b - a >= 3 for a, b in zip(spreads, spreads[1:]))
    assert not set(spreads) & {4, 5, 6}  # closed while the closure lasts


def test_shock_rolls_use_their_own_stream_so_arms_share_them():
    base = fg_env.load(OUTBREAK, seed=9)
    base.run("idle")
    calm = fg_env.load(OUTBREAK, seed=9, arm="calm")
    calm.run("idle")
    assert base.props["shocks"]["spreader"]["rounds"] == calm.props["shocks"]["spreader"]["rounds"]
    other = [fg_env.load(OUTBREAK, seed=s) for s in range(4, 9)]
    for env in other:
        env.run("idle")
    assert len({tuple(e.props["shocks"]["spreader"]["rounds"]) for e in other}) > 1


def test_manual_shock_and_errors():
    env = fg_env.load(OUTBREAK, seed=1)
    env.run("idle", rounds=1)
    env.effects.run([{"fire_shock": "alarm"}], {}, "t")
    assert env.props["alarm"] is True and env.props["shocks"]["alarm"]["count"] == 1
    bad = json.loads(json.dumps(OUTBREAK))
    bad["mechanisms"]["shocks"]["shocks"]["alarm"]["then"] = [{"shock": "alarms"}]
    with pytest.raises(ContractError, match="alarms"):
        fg_env.parse(bad)


PRIORS = {
    "name": "Uncertain",
    "clock": {"rounds": 2},
    "inputs": {"centre": {"type": "number", "default": 0.3}},
    "types": {"analyst": {"agent": True}},
    "entities": {"a": {"type": "analyst"}},
    "actions": {"wait": {"by": "analyst", "do": []}},
    "mechanisms": {"uncertainty": {"kind": "priors", "priors": {
        "beta_p": {"dist": "beta", "a": 9, "b": 21},
        "norm": {"dist": "normal", "mean": "$inputs.centre", "sd": 0.1, "min": 0, "max": 1},
        "uni": {"dist": "uniform", "low": 2, "high": 4},
        "logn": {"dist": "lognormal", "mu": 0, "sigma": 0.25},
        "tri": {"dist": "triangular", "low": 0, "mode": 1, "high": 3},
        "count": {"dist": "uniform", "low": 1, "high": 6, "integer": True},
        "regime": {"dist": "choice", "values": ["calm", "stormy"], "weights": [3, 1]},
    }}},
    "outputs": {"double": "$world.uni * 2"},
}


def test_priors_are_sampled_per_run_and_recorded_in_outputs():
    results = [fg_env.load(PRIORS, seed=s).run("idle") for s in range(12)]
    first = results[0].outputs
    assert set(first) >= {"beta_p", "norm", "uni", "logn", "tri", "count", "regime", "double"}
    assert first["double"] == pytest.approx(first["uni"] * 2)
    for result in results:
        o = result.outputs
        assert 0 <= o["beta_p"] <= 1 and 0 <= o["norm"] <= 1 and 2 <= o["uni"] <= 4 and o["logn"] > 0
        assert 0 <= o["tri"] <= 3 and o["count"] in range(1, 7) and o["regime"] in ("calm", "stormy")
    assert len({r.outputs["uni"] for r in results}) == 12
    assert fg_env.load(PRIORS, seed=5).run("idle").outputs == results[5].outputs
    assert abs(sum(r.outputs["norm"] for r in results) / 12 - 0.3) < 0.1


def test_prior_config_errors():
    bad = json.loads(json.dumps(PRIORS))
    bad["mechanisms"]["uncertainty"]["priors"]["beta_p"] = {"dist": "beta", "a": 9}
    with pytest.raises(ContractError, match="needs `b`"):
        fg_env.parse(bad)
    backwards = json.loads(json.dumps(PRIORS))
    backwards["mechanisms"]["uncertainty"]["priors"]["uni"] = {"dist": "uniform", "low": 5, "high": 1}
    with pytest.raises(ContractError, match="low"):
        fg_env.parse(backwards)


# ---------------------------------------------------------------------------
# procedure
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
    "mechanisms": {"hearing": {"kind": "procedure", "phases": {
        "opening": {"title": "Openings", "brief": "Give your opening.",
                    "stages": [{"who": "$it.type == lawyer", "actions": ["open"]}],
                    "on_enter": ["$world.entered += opening"], "on_exit": ["$world.exits += 1"],
                    "next": [{"to": "argument", "all_did": "open"}]},
        "argument": {"stages": [{"actions": ["argue"]}, {"name": "bell", "actions": ["ring"], "when": "$world.hearing_round == $inputs.days"}],
                     "on_enter": ["$world.entered += argument"],
                     "next": [{"to": "ruling", "event": "bell", "say": "Arguments are closed."}, {"to": "ruling", "after": 5}]},
        "ruling": {"stages": [{"actions": ["rule"]}], "terminal": True, "on_enter": ["$world.entered += ruling"]},
    }}},
    "outputs": {"entered": "$world.entered"},
}


def test_procedure_compiles_to_stages_that_follow_the_phase():
    contract = fg_env.parse(HEARING)
    names = [s.name for s in contract.stages]
    assert names == ["opening", "argument", "bell", "ruling"]
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
# turn_order
# ---------------------------------------------------------------------------

TABLE = {
    "name": "Table",
    "clock": {"rounds": 6},
    "world": {"calls": {"type": "list", "default": []}},
    "types": {"player": {"agent": True, "props": {"speed": 0, "folded": False, "lucky": False}}},
    "entities": {"a": {"type": "player", "props": {"speed": 1}}, "b": {"type": "player", "props": {"speed": 3}},
                 "c": {"type": "player", "props": {"speed": 2}}},
    "actions": {"act": {"by": "player", "do": ["$world.calls += $actor.id",
                                              {"if": "$actor.lucky", "then": ["$actor.lucky = false", {"extra_turn": "$actor", "order": "seats"}]}]}},
    "mechanisms": {"seats": {"kind": "turn_order", "among": "player", "by": "$it.speed", "skip": "$it.folded",
                             "extra_turns": True, "stage": {"actions": ["act"], "turns": "sequential"}}},
    "outputs": {"calls": "$world.calls"},
}


def _acting(wake):
    if any(t.name == "act" for t in wake.tools):
        wake.call("act", {})
    wake.end()


def test_initiative_skip_and_extra_turns():
    env = fg_env.load(TABLE, seed=1)
    env.run(_acting, rounds=1)
    assert env.props["calls"] == ["b", "c", "a"]
    env.effects.run(["$entity(c).folded = true", "$entity(a).lucky = true"], {}, "t")
    env.run(_acting, rounds=1)
    assert env.props["calls"][3:] == ["b", "a", "a"]


@pytest.mark.parametrize("options, expected", [
    ({"rotate": True}, [["b", "c", "a"], ["c", "a", "b"], ["a", "b", "c"]]),
    ({"snake": True}, [["b", "c", "a"], ["a", "c", "b"], ["b", "c", "a"]]),
    ({"by": None}, [["a", "b", "c"]] * 3),
])
def test_rotation_and_snake_order(options, expected):
    contract = json.loads(json.dumps(TABLE))
    seats = contract["mechanisms"]["seats"]
    for key, value in options.items():
        if value is None:
            seats.pop(key)
        else:
            seats[key] = value
    env = fg_env.load(contract, seed=1)
    env.run(_acting, rounds=3)
    calls = env.props["calls"]
    assert [calls[i:i + 3] for i in (0, 3, 6)] == expected


def test_rotation_advances_by_seat_when_a_member_is_skipped_mid_run():
    contract = json.loads(json.dumps(TABLE))
    seats = contract["mechanisms"]["seats"]
    seats.pop("by")
    seats["rotate"] = True
    env = fg_env.load(contract, seed=1)
    env.run(_acting, rounds=1)
    env.effects.run(["$entity(b).folded = true"], {}, "t")
    env.run(_acting, rounds=2)
    calls = env.props["calls"]
    # round 1 starts at a; round 2 would start at b (folded) so c leads; round 3 starts at c
    assert calls == ["a", "b", "c", "c", "a", "c", "a"]
    env.effects.run(["$entity(b).folded = false"], {}, "t")
    env.run(_acting, rounds=1)
    assert env.props["calls"][7:] == ["a", "b", "c"]  # round 4: the rotation is back to seat a


def test_hooks_refuse_an_action_whose_effects_are_not_a_list():
    bad = json.loads(json.dumps(ARENA))
    bad["actions"]["bash"]["do"] = {"apply": "stun", "to": "$params.target"}
    bad["mechanisms"]["abilities"] = {"kind": "cooldowns", "actions": {"bash": {"cooldown": 1}}}
    with pytest.raises(ContractError, match=r"actions\.bash\.do must be a list"):
        fg_env.parse(bad)
    otherwise = json.loads(json.dumps(ARENA))
    otherwise["actions"]["bash"].update(chance=0.5, otherwise="$actor.hp -= 1")
    otherwise["mechanisms"]["abilities"] = {"kind": "cooldowns", "actions": {"bash": {"cooldown": 1}}}
    with pytest.raises(ContractError, match=r"actions\.bash\.otherwise must be a list"):
        fg_env.parse(otherwise)


# ---------------------------------------------------------------------------
# victory
# ---------------------------------------------------------------------------

def _race(conditions, **extra):
    return {
        "name": "Race",
        "clock": {"rounds": 10},
        "world": {"heat": 0},
        "types": {"runner": {"agent": True, "props": {"score": 0, "hp": 3, "team": "red", "flags": 0}},
                  "monster": {"props": {"hp": 1}}},
        "entities": {"r1": {"type": "runner", "props": {"team": "red"}}, "r2": {"type": "runner", "props": {"team": "blue"}},
                     "r3": {"type": "runner", "props": {"team": "blue"}}, "m1": {"type": "monster"}},
        "actions": {"wait": {"by": "runner", "do": []}},
        "events": [{"phase": "end", "each": "runner", "do": ["$it.score += $i + 1"]},
                   {"phase": "end", "do": ["$world.heat += 1"]}],
        "mechanisms": {"win": {"kind": "victory", "players": "runner", "alive": "$it.hp > 0", "conditions": conditions, **extra}},
        "outputs": {"heat": "$world.heat"},
    }


def test_first_to_score_and_most_after_rounds():
    result = fg_env.run(_race([{"first_to": 6, "score": "$it.score"}]), seed=1)
    assert result.status == "ended" and result.winner == "r3" and result.rounds == 2
    result = fg_env.run(_race([{"most": "$it.score", "at": 4}]), seed=1)
    assert result.rounds == 4 and result.winner == "r3" and result.ended_by == "most"


def test_ties_share_draw_or_break():
    tied = _race([{"first_to": 1, "score": "$it.hp"}])
    assert sorted(fg_env.run(tied, seed=1).winner) == ["r1", "r2", "r3"]
    tied["mechanisms"]["win"]["ties"] = "draw"
    assert fg_env.run(tied, seed=1).winner is None
    tied["mechanisms"]["win"]["tiebreak"] = ["$it.team"]
    tied["mechanisms"]["win"]["ties"] = "share"
    assert fg_env.run(tied, seed=1).winner == "r1"  # 'red' sorts above 'blue'; higher wins
    tied["mechanisms"]["win"]["tiebreak"] = []
    tied["mechanisms"]["win"]["ties"] = "random"
    winners = {fg_env.run(tied, seed=s).winner for s in range(12)}
    assert winners <= {"r1", "r2", "r3"} and len(winners) > 1


def test_last_standing_team_cooperative_stable_eliminate_objectives():
    standing = _race([{"last_standing": True}])
    standing["events"].append({"at": 3, "do": ["$entity(r1).hp = 0", "$entity(r2).hp = 0"]})
    result = fg_env.run(standing, seed=1)
    assert result.winner == "r3" and result.rounds == 3 and result.ended_by == "last_standing"

    team = _race([{"last_team": "$it.team"}])
    team["events"].append({"at": 2, "do": ["$entity(r1).hp = 0"]})
    assert fg_env.run(team, seed=1).winner == "blue"

    coop = _race([{"lose_when": "$world.heat >= 5", "say": "Too hot."}, {"win_when": "$world.heat >= 3"}])
    result = fg_env.run(coop, seed=1)
    assert result.ended_by == "win" and sorted(result.winner) == ["r1", "r2", "r3"]

    stable = _race([{"stable": "$world.heat >= 2", "rounds": 3}])
    assert fg_env.run(stable, seed=1).rounds == 4

    slay = _race([{"eliminate": "monster", "winner": "$entity(r1)"}])
    slay["events"].append({"at": 5, "do": [{"remove": "$entity(m1)"}]})
    result = fg_env.run(slay, seed=1)
    assert result.rounds == 5 and result.winner == "r1" and result.ended_by == "eliminate"

    goals = _race([{"objectives": ["$it.score >= 5", "$it.team == blue"]}])
    assert fg_env.run(goals, seed=1).winner == "r3"


def test_victory_errors():
    with pytest.raises(ContractError, match="exactly one"):
        fg_env.parse(_race([{"first_to": 3, "most": "$it.score"}]))
    with pytest.raises(ContractError, match="score"):
        fg_env.parse(_race([{"first_to": 3}]))


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------

FIELD = {
    "name": "Field",
    "clock": {"rounds": 5},
    "space": {"grid": {"rows": 3, "cols": 3}},
    "types": {"scout": {"agent": True, "props": {"hp": 10, "stealth": 1, "boots": False}}},
    "entities": {"s": {"type": "scout", "at": [0, 0]}},
    "actions": {"go": {"by": "scout", "params": {"row": {"type": "int", "min": 0, "max": 2}, "col": {"type": "int", "min": 0, "max": 2}},
                       "do": [{"enter": "$actor", "to": "[$params.row, $params.col]"}]}},
    "mechanisms": {"terrain": {"kind": "locations", "occupants": "scout", "places": {
        "forest": {"area": [[0, 1], [1, 2]], "props": {"cover": 2}, "modifiers": {"stealth": 2}},
        "lava": {"at": [[2, 2]], "enter": [{"expr": "$it.boots", "why": "You need fire boots."}], "tick": ["$it.hp -= 3"],
                 "on_enter": ["$it.hp -= 1"]},
    }}},
    "outputs": {"hp": "$entity(s).hp"},
}


def test_terrain_modifiers_entry_rules_and_ticks():
    env = fg_env.load(FIELD, seed=1)
    world = env.world
    from fg_env.sdk.expr import compile_expr
    play = _script({("s", 1): [("go", {"row": 2, "col": 2}), ("go", {"row": 1, "col": 1})]})
    env.run(play, rounds=1)
    assert play.log[0][3] is False and "fire boots" in play.log[0][4]
    assert env.entity("s")["at"] == [1, 1]
    assert compile_expr("$effective($entity(s), 'stealth')")(world.scope()) == 3
    assert compile_expr("$terrain($entity(s)).cover")(world.scope()) == 2
    assert compile_expr("$terrain([0, 0])")(world.scope()) is None
    assert compile_expr("$can_enter($entity(s), [2, 2])")(world.scope()) is False
    env.effects.run(["$entity(s).boots = true", {"enter": "$entity(s)", "to": [2, 2]}], {}, "t")
    assert _props(env, "s")["hp"] == 9
    env.run("idle", rounds=2)
    assert _props(env, "s")["hp"] == 3


def test_terrain_errors():
    bad = json.loads(json.dumps(FIELD))
    bad["mechanisms"]["terrain"]["places"]["lava"]["at"] = [[5, 5]]
    with pytest.raises(ContractError, match="off the 3x3 grid"):
        fg_env.parse(bad)


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


def test_guide_documents_the_new_kinds_and_functions():
    text = fg_env.guide("mechanisms")
    for kind in ("status_effects", "cooldowns", "channeling", "drift", "shocks", "priors", "procedure",
                 "turn_order", "victory", "locations"):
        assert f"### `{kind}`" in text
    functions = fg_env.guide("functions")
    for name in ("$effective(", "$has_status(", "$ready(", "$prior(", "$winner(", "$terrain(", "$turn_rank("):
        assert name in functions


# ---------------------------------------------------------------------------
# Acceptance examples
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["dungeon_skirmish", "civil_trial_procedure", "epidemic_shocks"])
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


def test_dungeon_skirmish_uses_every_tactical_family():
    contract = fg_env.parse(EXAMPLES / "dungeon_skirmish.json")
    kinds = {m["kind"] for m in contract.mechanisms.values()}
    assert kinds >= {"status_effects", "cooldowns", "channeling", "locations", "victory", "turn_order"}
    result = fg_env.load(EXAMPLES / "dungeon_skirmish.json", seed=3).run()
    assert result.status in ("completed", "ended"), result.error


def _content_size(path):
    """Bytes of the contract as data (minified), so nesting depth does not count as size."""
    return len(json.dumps(json.loads(path.read_text()), separators=(",", ":"), ensure_ascii=False))


def test_civil_trial_procedure_reaches_a_verdict_and_is_smaller():
    procedure = EXAMPLES / "civil_trial_procedure.json"
    assert _content_size(procedure) < _content_size(EXAMPLES / "civil_trial.json")
    result = fg_env.load(procedure, seed=2).run()
    assert result.status == "ended", result.error
    assert result.outputs["verdict"] in ("liable", "not_liable", "hung")
    phases = result.outputs["phases"]
    assert phases[:2] == ["opening", "direct"] and "cross" in phases and "deliberation" in phases


def test_epidemic_shocks_exposes_priors_and_varies_by_seed():
    path = EXAMPLES / "epidemic_shocks.json"
    outputs = [fg_env.load(path, seed=s, inputs={"residents": 60}).run(rounds=20).outputs for s in range(3)]
    assert len({o["transmissibility"] for o in outputs}) == 3
    assert all(math.isfinite(o["transmissibility"]) for o in outputs)
    assert all("superspreader_events" in o for o in outputs)
