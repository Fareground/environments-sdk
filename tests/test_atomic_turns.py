"""Atomic turns: a turn's actions apply together, and a turn that breaks `valid` is undone whole."""
import copy
import json

import fg_env

WALK = {
    "name": "Even steps",
    "clock": {"rounds": 1},
    "world": {"moves": 0, "alarms": 0, "idle": 0},
    "types": {"walker": {"agent": True, "props": {"pos": 0}}},
    "entities": {"ann": {"type": "walker", "name": "Ann"}, "bo": {"type": "walker", "name": "Bo"}},
    "actions": {
        "step": {"by": "walker", "description": "Step forward.",
                 "params": {"by": {"type": "int", "min": 1, "max": 5}},
                 "do": ["$actor.pos += $params.by", "$world.moves += 1"]},
        "ping": {"by": "walker", "when": "$actor.id == ann",
                 "do": [{"wake": "bo", "why": "Ann pinged.", "now": True}]},
    },
    "stages": [{"name": "walk", "max_actions": 2, "on_idle": ["$world.idle += 1"],
                "valid": [{"expr": "$actor.pos % 2 == 0", "why": "You must end on an even square, not {$actor.pos}"}]}],
}


def test_a_turn_that_breaks_valid_is_undone_whole_and_the_agent_plays_it_again():
    seen = []

    def ann(wake):
        seen.append(next(t.description for t in wake.tools if t.name == "end_turn"))
        seen.append(wake.call("step", {"by": 1}))
        seen.append(wake.me["pos"])  # applied at once: the agent sees its move
        seen.append(wake.call("end_turn"))
        seen.append(wake.me["pos"])
        seen.append(wake.call("step", {"by": 1}))
        seen.append(wake.call("step", {"by": 3}))

    env = fg_env.load(WALK, seed=1)
    result = env.run({"ann": ann, "bo": "idle"})
    end_text, first, moved, ended, undone_pos, again, last = seen
    assert "checked together" in end_text
    assert first.ok and moved == 1
    assert not ended.ok and not ended.ended and ended.data["error"] == "undone"
    assert "You must end on an even square, not 1" in ended.text and "undone" in ended.text
    assert undone_pos == 0
    assert again.ok and last.ok and last.ended
    assert env.entity("ann")["props"]["pos"] == 4 and env.props["moves"] == 2
    assert result.stats["undone_turns"] == 1 and result.stats["actions"] == 2


def test_triggers_and_invariants_wait_for_the_whole_turn():
    contract = copy.deepcopy(WALK)
    contract["triggers"] = [{"when": "$world.moves == 1", "do": ["$world.alarms += 1"]}]
    contract["invariants"] = ["$all(walker, $it.pos != 1)"]

    def two_steps(wake):
        wake.call("step", {"by": 1})
        wake.call("step", {"by": 1})

    atomic = fg_env.load(contract, seed=1)
    assert atomic.run({"ann": two_steps, "bo": "idle"}).ok
    assert atomic.props["alarms"] == 0 and atomic.entity("ann")["props"]["pos"] == 2

    loose = copy.deepcopy(contract)
    del loose["stages"][0]["valid"]
    result = fg_env.load(loose, seed=1).run({"ann": two_steps, "bo": "idle"})
    assert (result.status == "completed" and result.stats["faulted_actions"]
            == 2)  # each step breaks it at once: refused and undone
    assert any(d["code"] == "action_broke_invariant" for d in result.diagnostics)


def test_an_open_turn_left_invalid_is_undone_when_the_participant_returns():
    def lazy(wake):
        wake.call("step", {"by": 3})  # one of two actions, then returns without ending the turn

    env = fg_env.load(WALK, seed=1)
    result = env.run({"ann": lazy, "bo": "idle"})
    assert env.entity("ann")["props"]["pos"] == 0 and env.props["moves"] == 0
    told = [e for e in result.events if e["kind"] == "outcome" and e.get("to") == ["ann"]]
    assert told[-1]["text"] == "Your turn was undone: You must end on an even square, not 3."
    assert env.props["idle"] == 2  # an undone turn took no action, so on_idle runs (Bo idles too)


def test_reactions_wait_for_the_turn_to_commit_and_never_happen_for_an_undone_turn():
    woken = []
    env = fg_env.load(WALK, seed=1)

    def ann(wake):
        wake.call("ping")
        wake.call("step", {"by": 1})  # second action: the turn settles, is undone, and the ping with it
        wake.call("ping")
        wake.call("step", {"by": 2})

    def bo(wake):
        if wake.reason == "Ann pinged.":
            woken.append(env.entity("ann")["props"]["pos"])
        wake.end()

    assert env.run({"ann": ann, "bo": bo}).ok
    assert woken == [2]  # once, after the valid turn committed


def test_in_a_simultaneous_stage_each_agents_choices_stand_or_fall_together():
    sealed = copy.deepcopy(WALK)
    sealed["stages"][0]["turns"] = "simultaneous"

    def participant(wake):
        steps = [1, 2] if wake.entity_id == "ann" else [2]
        for size in steps:
            wake.call("step", {"by": size})
        wake.end()

    env = fg_env.load(sealed, seed=1)
    result = env.run(participant)
    assert env.entity("ann")["props"]["pos"] == 0 and env.entity("bo")["props"]["pos"] == 2
    told = [e["text"] for e in result.events if e["kind"] == "outcome" and e.get("to") == ["ann"]]
    assert told[-1] == "Your choices were undone: You must end on an even square, not 3."
    assert result.stats["undone_turns"] == 1 and env.props["idle"] == 1


def test_random_atomic_runs_resume_exactly_from_a_snapshot():
    contract = copy.deepcopy(WALK)
    contract["clock"]["rounds"] = 4
    straight = fg_env.load(contract, seed=8).run()
    env = fg_env.load(contract, seed=8)
    env.run(rounds=2)
    resumed = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert resumed.run().to_dict() == straight.to_dict()


def test_valid_is_checked_like_any_rule():
    contract = copy.deepcopy(WALK)
    contract["stages"][0]["valid"] = [{"expr": "$target.pos > 0"}]
    errors = [str(i) for i in fg_env.check(contract) if i.severity == "error"]
    assert any("stages[0].valid[0]" in e and "$target is not available" in e for e in errors)
    shorthand = copy.deepcopy(WALK)
    shorthand["stages"][0]["valid"] = "$actor.pos >= 0"
    assert [i for i in fg_env.check(shorthand) if i.severity == "error"] == []


PEEK = {
    "name": "Peek", "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"coins": 10, "peeks": 0}}}, "entities": {"a": {"type": "p"}},
    "world": {"card": "ace"},
    "actions": {"peek": {"by": "p", "when": "$actor.coins >= 5", "do": ["$actor.coins -= 5", "$actor.peeks += 1"],
                         "outcome": "The top card is {$world.card}."},
                "splurge": {"by": "p", "do": ["$actor.coins -= 100"]}},
    "stages": [{"name": "s", "max_actions": 3, "valid": {"expr": "$actor.coins >= 0", "why": "no debt"}}],
}


def test_what_an_undone_turn_showed_its_agent_is_never_shown():
    replies = []

    def cheat(wake):  # peek, then break the turn on purpose so the peek is refunded
        replies.extend([wake.call("peek"), wake.call("splurge"), wake.call("end_turn")])

    env = fg_env.load(PEEK, seed=2)
    env.run(cheat)
    assert env.entity("a")["props"] == {"coins": 10, "peeks": 0}
    assert not any("ace" in reply.text for reply in replies), replies
    assert replies[0].ok and "when your turn ends" in replies[0].text


def test_an_atomic_turn_shows_its_outcomes_once_it_commits():
    replies = []

    def honest(wake):
        replies.extend([wake.call("peek"), wake.call("end_turn")])

    fg_env.load(PEEK, seed=2).run(honest)
    assert replies[1].ok and replies[1].text == "The top card is ace. Turn ended."
