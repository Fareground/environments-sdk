"""Turn flow: reactive triggers, out-of-turn reactions and auto-played trivial turns."""
import json

import fg_env

HALT = {
    "name": "Halt", "clock": {"rounds": 3},
    "world": {"price": 100, "halted": False, "halts": 0},
    "types": {"trader": {"agent": True}},
    "population": [{"type": "trader", "count": 3}],
    "actions": {"dump": {"by": "trader", "when": {"expr": "not $world.halted", "why": "Trading is halted."},
                         "do": ["$world.price -= 6"], "terminal": True}},
    "triggers": [{"name": "breaker", "when": "$world.price <= 90", "do": ["$world.halted = true", "$world.halts += 1"],
                  "say": "Circuit breaker: trading halted at {$world.price}."}],
    "stages": [{"name": "trade", "turns": "sequential"}],
}


def _dump(wake):
    if "dump" in {t.name for t in wake.tools}:
        wake.call("dump")
    wake.end()


def test_a_trigger_fires_the_moment_its_condition_becomes_true():
    env = fg_env.load(HALT, seed=1)
    result = env.run(_dump, rounds=1)
    assert env.props["price"] == 88 and env.props["halted"] and env.props["halts"] == 1  # the third trader was stopped
    news = [e for e in result.events if e["kind"] == "news"]
    assert news and "halted at 88" in news[0]["text"]
    env.world.props["price"] = 100
    env.world.props["halted"] = False
    env.run(_dump, rounds=1)
    assert env.props["halts"] == 2  # re-armed after the condition was false


def test_trigger_state_survives_snapshots():
    straight = fg_env.load(HALT, seed=2).run(_dump).to_dict()
    env = fg_env.load(HALT, seed=2)
    env.run(_dump, rounds=1)
    assert fg_env.Env.restore(HALT, json.loads(json.dumps(env.snapshot()))).run(_dump).to_dict() == straight


def test_trigger_chains_deeper_than_the_limit_are_an_error():
    chain = [{"when": f"$world.halts == {k}", "do": [f"$world.halts = {k + 1}"]} for k in range(12)]
    result = fg_env.load({**HALT, "triggers": chain}, seed=1).run(_dump, rounds=1)
    assert result.status == "failed" and "levels deep" in result.error
    short = [{"when": f"$world.halts == {k}", "do": [f"$world.halts = {k + 1}"]} for k in range(4)]
    env = fg_env.load({**HALT, "triggers": short}, seed=1)
    assert env.run(_dump, rounds=1).status == "running" and env.props["halts"] == 4


COURT = {
    "name": "Court", "clock": {"rounds": 1},
    "world": {"log": {"type": "list", "default": []}},
    "types": {"lawyer": {"agent": True}, "judge": {"agent": True}},
    "entities": {"a": {"type": "lawyer"}, "b": {"type": "lawyer"}, "j": {"type": "judge"}},
    "actions": {
        "speak": {"by": "lawyer", "do": ["$world.log += $actor.id + ' speaks'"], "terminal": True},
        "object": {"by": "lawyer", "do": ["$world.log += $actor.id + ' objects'",
                                         {"wake": "$entity(j)", "now": True,
                                          "why": "An objection needs a ruling now."}]},
        "rule": {"by": "judge", "do": ["$world.log += 'judge rules'"], "terminal": True},
        "wait": {"by": "judge", "terminal": True},
    },
    "stages": [{"name": "hearing", "turns": "sequential", "actions": {"lawyer": ["speak", "object"], "judge": ["wait"]},
                "max_actions": 2}],
}


def test_wake_now_gives_a_reaction_turn_after_the_action_and_before_the_turn_continues():
    reasons, seen = [], []

    def play(wake):
        if wake.entity_id == "a":
            wake.call("object")
            wake.call("speak")
        elif wake.entity_id == "j":
            reasons.append(wake.reason)
            seen.append(list(env.props["log"]))
            names = {t.name for t in wake.tools}
            wake.call("rule" if "rule" in names else "wait")
        else:
            wake.call("speak")
        wake.end()

    contract = json.loads(json.dumps(COURT))
    contract["stages"][0]["actions"]["judge"] = ["rule", "wait"]
    env = fg_env.load(contract, seed=1)
    result = env.run(play)
    assert result.status == "completed", result.error
    assert env.props["log"][:3] == ["a objects", "judge rules", "a speaks"]
    assert reasons[0] == "An objection needs a ruling now."
    assert seen[0] == ["a objects"]  # the action that woke the judge had already taken effect
    assert result.stats["reactions"] == 1


def test_two_triggers_that_undo_each_other_settle_instead_of_looping():
    c = {"name": "PingPong", "clock": {"rounds": 2}, "world": {"x": 0, "hits": 0},
         "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
         "actions": {"poke": {"by": "p", "do": "$world.x = 1"}},
         "triggers": [{"when": "$world.x == 1", "do": ["$world.x = 0", "$world.hits += 1"]},
                      {"when": "$world.x == 0 && $world.hits > 0", "do": "$world.x = 1"}]}
    result = fg_env.load(c, seed=1).run(lambda wake: wake.call("poke", {}))
    assert result.status == "completed", result.error
