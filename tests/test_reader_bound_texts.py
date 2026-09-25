"""Every text an agent reads is rendered for its reader, so it never shows another agent's private value.

Each text here was once rendered in the true state, as game logic reads it: a stage's `valid` why, an end's `say`
(a condition's or an `end` effect's), a `wake`'s why, a status's news, a procedure's news and its stack's items, a
card reveal's `say`, a pot's showdown labels, a ballot's announcement and a chance node's labels. Ana and Bo each keep
a private `secret`; a template that reads Bo's where Ana (or everyone) reads it fails like every other agent-facing
text, and Bo's secret appears nowhere. What static analysis can see is refused before the run; the probes that test
the run's own gate fetch Bo's secret with `$get`, which it cannot follow (a plain `$entity(b).secret` is a check error).
"""
import pytest

import fg_env

ANA, BO = "1111", "9999"


def contract(**overrides):
    c = {
        "name": "Secrets", "clock": {"rounds": 1},
        "types": {"p": {"agent": True, "props": {"seat": 0, "secret": {"type": "int", "default": 0, "private": True}}}},
        "entities": {"a": {"type": "p", "name": "Ana", "props": {"seat": 0, "secret": int(ANA)}},
                     "b": {"type": "p", "name": "Bo", "props": {"seat": 1, "secret": int(BO)}}},
        "world": {"hoard": {"type": "int", "default": 7777, "private": True}},
        "stages": [{"name": "act", "actions": ["poke"]}],
        "actions": {"poke": {"by": "p", "do": []}},
        "outputs": {"seats": "$sum(p, $it.seat)"},
    }
    c.update(overrides)
    return c


def play(c, calls=None):
    """Run ``c``; each agent reads everything it is offered and makes its ``calls`` (name → args, by agent id).
    The result, and every text any agent read or was sent."""
    read: list[str] = []

    def participant(wake):
        read.extend([wake.brief, wake.update])
        for name, args in (calls or {}).get(wake.entity_id, []):
            if wake.done:
                break
            if any(tool.name == name for tool in wake.tools):
                read.append(wake.call(name, args).text)
        if not wake.done:
            wake.end()

    result = fg_env.load(c, seed=1).run(participant)
    read += [event.get("text") or "" for event in result.events]
    return result, "\n".join(read)


def refused_at_runtime(c, calls=None):
    """The render that would have shown Bo's secret is refused as ``c`` runs: the run fails, or the action it was part
    of is refused as the rule's fault (reported in the diagnostics, and by the check's smoke run); no agent reads the
    secret."""
    result, read = play(c, calls)
    reported = "\n".join([result.error or "", *(d["message"] for d in result.diagnostics),
                          *(str(issue) for issue in fg_env.check(c))])
    assert BO not in read and BO not in reported
    assert "Bo's secret is private" in reported, (result.status, reported, read)
    return result


def refused_by_check(c, path):
    errors = [i for i in fg_env.check(c) if i.severity == "error" and i.path == path]
    assert errors and "private" in str(errors[0]), fg_env.check(c)
    with pytest.raises(fg_env.ContractError):
        fg_env.load(c)


# -- a stage's `valid` why: the acting agent reads it --------------------------------------------------------------

def _valid(why):
    return contract(stages=[{"name": "act", "actions": ["poke"], "who": "$it.id == a",
                             "valid": [{"expr": "false", "why": why}]}])


def test_a_valid_why_shows_the_actor_its_own_private_value():
    result, read = play(_valid("{$actor.secret} is yours"), {"a": [("poke", {})]})
    assert f"{ANA} is yours" in read and BO not in read, read


def test_a_valid_why_is_refused_another_agents_private_value():
    refused_at_runtime(_valid("{$get($entity(b), secret)}"), {"a": [("poke", {})]})


def test_a_valid_why_reading_a_private_world_value_is_refused_by_the_check():
    refused_by_check(_valid("{$world.hoard}"), "stages[0].valid[0].why")


# -- an end's `say`: the run's last news, to everyone --------------------------------------------------------------

def test_an_end_conditions_say_is_refused_a_private_value():
    c = contract(end=[{"when": "$round >= 1", "say": "Over: {$get($entity(b), secret)}"}])
    assert refused_at_runtime(c).status == "failed"


def test_an_end_conditions_say_reading_a_private_world_value_is_refused_by_the_check():
    refused_by_check(contract(end=[{"when": "$round >= 1", "say": "{$world.hoard}"}]), "end[0].say")


def test_an_end_effects_say_is_refused_a_private_value():
    c = contract(actions={"poke": {"by": "p", "do": [{"end": "quit", "say": "{$get($entity(b), secret)}"}]}})
    refused_at_runtime(c, {"a": [("poke", {})]})


def test_an_end_effects_say_reading_the_actors_private_value_is_refused_by_the_check():
    c = contract(actions={"poke": {"by": "p", "do": [{"end": "quit", "say": "{$actor.secret}"}]}})
    refused_by_check(c, "actions.poke.do[0].say")


def test_an_invariants_why_reading_a_private_world_value_is_refused_by_the_check():
    refused_by_check(contract(invariants=[{"expr": "true", "why": "{$world.hoard}"}]), "invariants[0].why")


# -- a `wake`'s why: the woken agent reads it ----------------------------------------------------------------------

def test_a_wake_why_shows_the_woken_agent_its_own_private_value_and_no_other():
    wake = {"poke": {"by": "p", "do": [{"wake": "$entity(b)", "now": True,
                                        "why": "{$get($entity(b), secret)} is yours"}]}}
    c = contract(actions=wake, stages=[{"name": "act", "actions": ["poke"], "who": "$it.id == a"}])
    result, read = play(c, {"a": [("poke", {})]})
    assert result.status == "completed", result.error
    assert f"{BO} is yours" in read  # Bo, woken, is told its own
    wake["poke"]["do"][0]["wake"] = "$entity(a)"  # Ana is woken with Bo's secret: refused
    refused_at_runtime(c, {"a": [("poke", {})]})


# -- mechanisms' news, sent to everyone ----------------------------------------------------------------------------

def _status(say):
    return contract(
        actions={"poke": {"by": "p", "do": [{"game": "conditions", "action": "apply", "status": "hurt",
                                             "who": "$actor"}]}},
        mechanisms={"conditions": {"kind": "game", "mode": "status", "who": "p",
                                   "statuses": {"hurt": {"duration": 1, "say": say}}}})


def test_a_status_say_is_refused_a_private_value():
    refused_at_runtime(_status("{$it.name} is hurt ({$get($entity(b), secret)})"), {"a": [("poke", {})]})


def test_a_status_say_reading_its_carriers_private_value_is_refused_by_the_check():
    refused_by_check(_status("{$it.secret}"), "mechanisms.conditions.statuses.hurt.say")


def _procedure(say):
    return contract(mechanisms={"hearing": {"kind": "decision", "mode": "procedure", "phases": {
        "open": {"stages": [{"actions": ["poke"]}], "say": say, "next": [{"to": "shut", "after": 1}]},
        "shut": {"stages": [{"actions": ["poke"]}], "terminal": True}}}}, stages=[])


def test_a_procedure_phases_say_is_refused_a_private_value():
    refused_at_runtime(_procedure("Open: {$get($entity(b), secret)}"))


def test_a_procedure_phases_say_reading_a_private_world_value_is_refused_by_the_check():
    refused_by_check(_procedure("{$world.hoard}"), "mechanisms.hearing.phases.open.say")


def _stack(show):
    kinds = {"claim": {"responders": "false", "params": {"n": "int"}, "show": show, "resolve": []}}
    trial = {"kind": "decision", "mode": "procedure", "stack": {"who": ["p"], "kinds": kinds},
             "phases": {"open": {"stages": [{"actions": ["trial_claim"]}]}}}
    return contract(mechanisms={"trial": trial}, stages=[])


def test_a_stack_items_show_is_refused_a_private_value_in_the_news_and_the_view():
    refused_at_runtime(_stack("n {$params.n} ({$get($entity(b), secret)})"), {"a": [("trial_claim", {"n": 3})]})


def test_a_stack_items_show_reading_its_pushers_private_value_is_refused_by_the_check():
    refused_by_check(_stack("{$actor.secret}"), "mechanisms.trial.stack.kinds.claim.show")


def _cards(to=None):
    reveal = {"game": "cards", "action": "reveal", "cards": "$hand($actor)", "say": "{$get($entity(b), secret)}"}
    if to is not None:
        reveal["to"] = to
    return contract(mechanisms={"cards": {"kind": "game", "mode": "cards", "who": "p", "hand_size": 1}},
                    actions={"poke": {"by": "p", "do": [reveal]}})


def test_a_card_reveals_say_is_refused_a_private_value_for_everyone_and_for_one_player():
    refused_at_runtime(_cards(), {"a": [("poke", {})]})
    refused_at_runtime(_cards(to="$entity(a)"), {"a": [("poke", {})]})


def test_a_card_reveals_say_to_its_one_reader_shows_it_its_own():
    c = _cards(to="$entity(b)")
    result, read = play(c, {"a": [("poke", {})]})
    assert result.status == "completed", result.error
    assert BO in read  # told to Bo alone


def test_a_card_reveals_say_to_everyone_reading_a_private_world_value_is_refused_by_the_check():
    c = _cards()
    c["actions"]["poke"]["do"][0]["say"] = "{$world.hoard}"
    refused_by_check(c, "actions.poke.do[0].say")


def test_a_pots_showdown_label_is_refused_a_private_value():
    c = contract(mechanisms={"table": {"kind": "game", "mode": "pot", "who": "p", "seat": "$it.seat", "stack": 100,
                                       "score": "$it.seat", "label": "$get($entity(b), secret)",
                                       "streets": {"betting": []}, "blinds": [1, 2]}}, stages=[], actions={})
    refused_at_runtime(c, {pid: [("table_call", {}), ("table_check", {})] for pid in ("a", "b")})


def test_a_ballots_announcement_is_refused_a_private_value():
    c = contract(mechanisms={"poll": {"kind": "decision", "mode": "ballot", "who": "p", "options": ["yes", "no"],
                                      "announce": "Decided ({$get($entity(b), secret)})"}}, stages=[])
    refused_at_runtime(c, {pid: [("poll_vote", {"choice": "yes"})] for pid in ("a", "b")})


def _chance(label):
    return contract(actions={"poke": {"by": "p", "do": [
        {"chance": [{"p": 1, "label": label, "do": []}], "as": "luck"}], "outcome": "Drew {$luck}."}})


def test_a_chance_label_is_refused_a_private_value():
    refused_at_runtime(_chance("{$get($entity(b), secret)}"), {"a": [("poke", {})]})


def test_a_chance_label_reading_the_actors_private_value_is_refused_by_the_check():
    refused_by_check(_chance("{$actor.secret}"), "actions.poke.do[0].chance[0].label")

