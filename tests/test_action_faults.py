"""A rule that fails, or an invariant that breaks, while an agent's action applies refuses that action and undoes it
whole; the run goes on. The same failure outside an agent's action is the contract's bug and fails the run."""
import copy

import fg_env

CALC = {
    "name": "Calculator",
    "clock": {"rounds": 2},
    "world": {"x": 0.0, "hits": 0, "secret": 7},
    "types": {"p": {"agent": True, "props": {"used": 0}}},
    "entities": {"a": {"type": "p"}},
    "actions": {
        "div": {"by": "p", "params": {"n": "number"},
                "do": ["$actor.used += 1", "$world.x = 10 / $params.n"]},
        "big": {"by": "p", "params": {"n": "number"}, "do": ["$actor.used += 1", "$world.x = $params.n ** 400"]},
        "tick": {"by": "p", "do": "$world.hits += 1"},
    },
    "stages": [{"name": "play", "max_actions": 5}],
    "outputs": {"x": "$world.x", "hits": "$world.hits"},
}


def _calls(*calls):
    seen = []

    def play(wake):
        for name, args in calls:
            seen.append(wake.call(name, args))
        wake.call("end_turn")
    return play, seen


def test_a_division_by_zero_refuses_the_action_undoes_it_and_the_run_goes_on():
    play, seen = _calls(("div", {"n": 0}), ("tick", {}), ("div", {"n": 4}))
    env = fg_env.load(CALC, seed=1)
    result = env.run(play, rounds=1)
    refused, ticked, divided = seen
    assert not refused.ok and not refused.ended and refused.data["error"] == "rejected"
    assert "could not be worked out" in refused.text and "Nothing changed" in refused.text
    assert "$params" not in refused.text and "actions.div" not in refused.text  # no rule internals for the agent
    assert ticked.ok and divided.ok
    assert env.entity("a")["props"]["used"] == 1  # the refused call's first effect was undone too
    assert env.props["x"] == 2.5 and env.props["hits"] == 1
    assert result.status == "running" and result.error is None
    assert result.stats["rejected_actions"] == 1 and result.stats["faulted_actions"] == 1
    finding = next(d for d in result.diagnostics if d["code"] == "action_rule_failed")
    assert finding["path"] == "actions.div.do[1]" and "division by zero" in finding["message"]
    assert "1 time" in finding["message"] and "min" in finding["fix"]


def test_an_overflow_refuses_the_action_and_the_run_completes():
    play, seen = _calls(("big", {"n": 1e10}))
    result = fg_env.load(CALC, seed=1).run(play)
    assert result.status == "completed"
    assert not seen[0].ok and "could not be worked out" in seen[0].text
    assert result.outputs["x"] == 0 and result.stats["faulted_actions"] == 2


def test_the_agent_is_never_shown_hidden_values_from_a_failing_rule():
    contract = copy.deepcopy(CALC)
    contract["actions"]["peek"] = {"by": "p", "do": "$world.x = 'code ' + $world.secret"}
    play, seen = _calls(("peek", {}))
    result = fg_env.load(contract, seed=1).run(play, rounds=1)
    assert not seen[0].ok and "7" not in seen[0].text
    assert any("peek" in d["path"] for d in result.diagnostics)


def test_the_same_failure_in_an_event_still_fails_the_run_with_its_path():
    contract = copy.deepcopy(CALC)
    contract["events"] = [{"phase": "start", "do": "$world.x = 1 / $world.hits"}]
    result = fg_env.load(contract, seed=1).run("idle")
    assert result.status == "failed" and "events[0].do[0]" in result.error and "division by zero" in result.error


def test_a_trigger_the_action_sets_off_fails_with_it_and_is_undone_with_it():
    contract = copy.deepcopy(CALC)
    contract["triggers"] = [{"when": "$world.x > 1", "do": ["$world.hits += 1", "$world.hits += 1 / ($world.x - 2)"]}]
    play, seen = _calls(("div", {"n": 5}), ("div", {"n": 4}))
    env = fg_env.load(contract, seed=1)
    result = env.run(play, rounds=1)
    first, second = seen
    assert not first.ok and "could not be worked out" in first.text
    assert second.ok  # the trigger is armed again after the undo, so it fires for this action
    assert env.entity("a")["props"]["used"] == 1 and env.props["x"] == 2.5 and env.props["hits"] == 3
    assert result.error is None


INVARIANT = {
    "name": "Capped",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"c": 0}}},
    "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
    "invariants": [{"expr": "$all(p, $it.c <= 5)", "why": "nobody may hold more than 5"}],
    "actions": {"add": {"by": "p", "params": {"n": {"type": "int", "min": 1, "max": 10}},
                        "do": "$actor.c += $params.n"}},
    "stages": [{"name": "play", "max_actions": 5}],
    "outputs": {"total": "$sum(p, $it.c)"},
}


def test_an_invariant_an_action_breaks_refuses_it_with_the_invariants_reason():
    play, seen = _calls(("add", {"n": 9}), ("add", {"n": 2}))
    env = fg_env.load(INVARIANT, seed=1)
    result = env.run({"a": play, "b": "idle"})
    broke, kept = seen
    assert not broke.ok and "nobody may hold more than 5" in broke.text and "$all" not in broke.text
    assert kept.ok and env.entity("a")["props"]["c"] == 2
    assert result.status == "completed" and result.stats["faulted_actions"] == 1
    finding = next(d for d in result.diagnostics if d["code"] == "action_broke_invariant")
    assert finding["path"] == "invariants[0]" and "actions.add" in finding["message"] and "when" in finding["fix"]


def test_an_invariant_broken_by_world_logic_still_fails_the_run():
    contract = copy.deepcopy(INVARIANT)
    contract["events"] = [{"phase": "start", "do": "$entity(b).c = 9"}]
    result = fg_env.load(contract, seed=1).run("idle")
    assert result.status == "failed" and "invariants[0]" in result.error


def test_an_invariant_broken_by_physics_fails_the_run_and_is_not_blamed_on_an_action():
    contract = copy.deepcopy(INVARIANT)
    contract["clock"] = {"rounds": 3}
    contract["physics"] = {"vars": {"level": {"start": 0, "rate": "1"}}}
    contract["invariants"] = [{"expr": "$physics.level <= 1.5", "why": "the tank may not overflow"}]
    play, _ = _calls(("add", {"n": 1}))
    result = fg_env.load(contract, seed=1).run({"a": play, "b": "idle"})
    assert result.status == "failed" and "invariants[0]" in result.error
    assert "physics" in result.error and "actions.add" not in result.error


SEALED = {
    "name": "Sealed",
    "clock": {"rounds": 1},
    "world": {"x": 0.0},
    "types": {"p": {"agent": True, "props": {"c": 0}}},
    "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
    "invariants": [{"expr": "$entity(a).c + $entity(b).c <= 5", "why": "the pot holds at most 5"}],
    "stages": [{"name": "bid", "turns": "simultaneous", "order": "seat"}],  # a commits first
    "actions": {
        "add": {"by": "p", "params": {"n": {"type": "int", "min": 1, "max": 5}}, "do": "$actor.c += $params.n"},
        "div": {"by": "p", "params": {"n": "number"},
                "do": ["$actor.c += 1", "$world.x = 10 / ($params.n - $actor.c)"]},
    },
}


def test_a_sealed_choice_that_breaks_an_invariant_at_commit_is_refused_and_the_run_completes():
    a, _ = _calls(("add", {"n": 4}))
    b, submitted = _calls(("add", {"n": 3}))
    env = fg_env.load(SEALED, seed=1)
    result = env.run({"a": a, "b": b})
    assert submitted[0].ok  # accepted when sealed: the breach only shows once a's choice has committed
    assert result.status == "completed"
    assert env.entity("a")["props"]["c"] == 4 and env.entity("b")["props"]["c"] == 0
    told = [e for e in result.events if e["kind"] == "outcome" and e["actor"] == "b"]
    assert "the pot holds at most 5" in told[-1]["text"]
    assert result.stats["faulted_actions"] == 1


def test_a_sealed_choice_whose_rule_fails_at_commit_is_refused_and_undone():
    a, _ = _calls(("add", {"n": 1}))
    b, submitted = _calls(("div", {"n": 2}))  # at submit: 10 / (2 - 0 - 1); at commit a holds 1, so 10 / 0
    contract = copy.deepcopy(SEALED)
    contract["actions"]["div"]["do"] = ["$actor.c += 1", "$world.x = 10 / ($params.n - $entity(a).c - $actor.c)"]
    env = fg_env.load(contract, seed=1)
    result = env.run({"a": a, "b": b})
    assert submitted[0].ok
    assert result.status == "completed"
    assert env.entity("b")["props"]["c"] == 0 and env.props["x"] == 0
    told = [e for e in result.events if e["kind"] == "outcome" and e["actor"] == "b"]
    assert "could not be worked out" in told[-1]["text"]


def test_a_sealed_choice_that_fails_when_submitted_is_refused_at_once():
    b, submitted = _calls(("div", {"n": 1}))
    result = fg_env.load(SEALED, seed=1).run({"a": "idle", "b": b})
    assert not submitted[0].ok and "could not be worked out" in submitted[0].text
    assert result.status == "completed"


def test_an_atomic_turn_that_breaks_an_invariant_is_undone_whole():
    contract = copy.deepcopy(INVARIANT)
    contract["stages"] = [{"name": "s", "atomic": True, "max_actions": 3}]
    seen = []

    def play(wake):
        seen.extend([wake.call("add", {"n": 3}), wake.call("add", {"n": 3}), wake.call("end_turn")])
    env = fg_env.load(contract, seed=1)
    result = env.run({"a": play, "b": "idle"})
    first, second, ended = seen
    assert first.ok and second.ok
    assert not ended.ok and ended.data["error"] == "undone" and "nobody may hold more than 5" in ended.text
    assert env.entity("a")["props"]["c"] == 0 and result.status == "completed"
    assert result.stats["undone_turns"] == 1 and result.stats["faulted_actions"] == 1


def test_a_run_with_refused_failures_snapshots_and_continues_exactly():
    play, _ = _calls(("div", {"n": 0}), ("tick", {}))
    straight = fg_env.load(CALC, seed=3).run(play)
    first = fg_env.load(CALC, seed=3)
    first.run(play, rounds=1)
    resumed = fg_env.Env.restore(CALC, first.snapshot()).run(play)
    assert resumed.status == straight.status == "completed"
    assert resumed.outputs == straight.outputs and resumed.stats == straight.stats
    assert resumed.diagnostics == straight.diagnostics
    assert [e["text"] for e in resumed.events] == [e["text"] for e in straight.events]


def test_a_failed_action_undoes_every_kind_of_change_it_made():
    c = {"name": "Atom", "clock": {"rounds": 2}, "world": {"total": 0, "tags": {"type": "list", "default": []}},
         "types": {"p": {"agent": True, "props": {"cash": 10}}, "token": {}, "place": {}},
         "relations": {"trusts": {}}, "records": {"chat": {"fields": {"text": "text"}}},
         "entities": {"a": {"type": "p", "at": "home"}, "b": {"type": "p", "at": "home"},
                      "home": {"type": "place"}, "away": {"type": "place"}},
         "actions": {"mess": {"by": "p", "do": [
             "$world.total += 1", "$world.tags += x", {"create": "token", "count": 2}, {"post": "chat", "text": "hi"},
             {"emit": "news", "say": "it happened"}, {"link": "trusts", "from": "$actor", "to": "$entity(b)"},
             {"move": "$actor", "to": "$entity(away)"},
             {"transfer": "cash", "from": "$actor", "to": "$entity(b)", "amount": 5},
             {"after": 1, "do": ["$world.total += 1000"]}, {"remove": "$entity(b)"}, {"fail": "boom"}]}},
         "outputs": {"total": "$world.total"}}
    env = fg_env.load(c, seed=1)
    before = [(e["id"], e["alive"], e["at"], e["props"]) for e in env.entities(alive=False)]
    replies = []
    env.run({"a": lambda w: replies.append(w.call("mess", {})), "b": "idle"})
    assert [r.text for r in replies] == ["boom", "boom"]
    assert env.props == {"total": 0, "tags": []} and env.records("chat") == []
    assert [(e["id"], e["alive"], e["at"], e["props"]) for e in env.entities(alive=False)] == before
    assert not [e for e in env.result().events if e.get("text") == "it happened"]
