"""Hidden values cannot be read out by probing.

A call refused after its action rolled luck or its rules read a value hidden from the actor is played (the action is
spent), so an agent cannot guess a hidden value again and again for free; a refusal that could tell it nothing hidden
(a taken cell, a bad argument, a `when` over public state) stays free, and a `when` that reads a hidden value is a
check warning. Entries posted to a record everyone reads, and the names of sealed choices announced
to everyone, may not carry what is private either.
"""
import copy

import pytest

import fg_env

VAULT = {
    "name": "Vault",
    "clock": {"rounds": 2},
    "types": {"p": {"agent": True, "props": {"won": False, "code": {"type": "int", "default": 0, "private": True}}},
              "vault": {"props": {"code": {"type": "int", "default": 0, "private": True}}}},
    "entities": {"a": {"type": "p"}, "b": {"type": "p", "props": {"code": 6}},
                 "v": {"type": "vault", "props": {"code": 6}}},
    "actions": {
        "guess": {"by": "p", "description": "Guess the vault code (0-9). One guess per turn.",
                  "params": {"x": {"type": "int", "min": 0, "max": 9}},
                  "do": [{"if": "$params.x != $entity(v).code", "then": [{"fail": "Wrong code."}]},
                         "$actor.won = true"]},
        "duel": {"by": "p", "params": {"x": {"type": "int", "min": 0, "max": 9},
                                       "t": {"type": "entity", "of": "p", "where": "$it.id != $actor.id"}},
                 "do": [{"if": "$params.x != $params.t.code", "then": [{"fail": "Wrong."}]}, "$actor.won = true"]},
    },
    "outputs": {"won": "$entity(a).won"},
}


def _guesser(tool, extra=None):
    """Guesses 0, 1, 2, … across its turns, as many per turn as the turn allows."""
    tried = []

    def play(wake):
        while len(tried) < 10:
            x = len(tried)
            result = wake.call(tool, {"x": x, **(extra or {})})
            tried.append((x, result.ok))
            if result.ok or result.ended:
                break
    return play, tried


def test_a_refusal_from_inside_do_spends_the_action_so_a_hidden_value_cannot_be_guessed_for_free():
    play, tried = _guesser("guess")
    result = fg_env.run(VAULT, {"a": play, "b": "idle"}, seed=1)
    assert tried == [(0, False), (1, False)]  # one guess per turn, two rounds
    assert result.outputs["won"] is False


def test_a_refusal_that_reads_only_public_state_stays_free_to_retry():
    board = copy.deepcopy(VAULT)
    board["world"] = {"taken": {"type": "list", "default": [0, 1]}}
    board["actions"]["guess"]["do"] = [{"if": "$params.x in $world.taken", "then": [{"fail": "That cell is taken."}]},
                                       "$world.taken += $params.x"]
    play, tried = _guesser("guess")
    fg_env.run(board, {"a": play, "b": "idle"}, seed=1)
    assert tried[:3] == [(0, False), (1, False), (2, True)]  # two taken cells cost nothing; the third call plays


def test_a_refusal_that_reads_a_chosen_agents_private_property_spends_the_action_too():
    play, tried = _guesser("duel", {"t": "b"})
    fg_env.run(VAULT, {"a": play, "b": "idle"}, seed=1)
    assert tried == [(0, False), (1, False)]


def test_a_sealed_choice_refused_as_it_is_tried_is_spent_as_well():
    sealed = {**copy.deepcopy(VAULT), "stages": [{"name": "guessing", "turns": "simultaneous", "actions": ["guess"]}]}
    play, tried = _guesser("guess")
    fg_env.run(sealed, {"a": play, "b": "idle"}, seed=1)
    assert tried == [(0, False), (1, False)]


def test_argument_checks_and_when_requirements_stay_free_to_retry():
    free = copy.deepcopy(VAULT)
    free["actions"]["guess"]["when"] = [{"expr": "$params.x > 1", "why": "guesses start at 2"}]
    play, tried = _guesser("guess")
    fg_env.run(free, {"a": play, "b": "idle"}, seed=1)
    # 0 and 1 are refused by the requirement for free; 2 is a real (wrong) guess that ends the turn; 3 the next turn's
    assert tried == [(0, False), (1, False), (2, False), (3, False)]


PROBE = {
    "name": "Probe",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"secret": {"type": "int", "default": 0, "private": True}}}},
    "entities": {"a": {"type": "p"}, "b": {"type": "p", "props": {"secret": 9}}},
    "actions": {"pick": {"by": "p", "when": ["$entity(b).secret > 5"], "do": []},
                "probe": {"by": "p", "params": {"x": {"type": "int", "min": 0, "max": 10}},
                          "when": [{"expr": "$params.x < $entity(b).secret", "why": "no"}], "do": []},
                "count": {"by": "p", "when": ["$sum(p, $it.secret) > 3"], "do": []},
                "mine": {"by": "p", "when": ["$actor.secret > 3"], "do": []}},
}


def test_check_warns_of_a_requirement_reading_another_agents_private_state_however_it_is_reached():
    warned = {issue.path for issue in fg_env.check(PROBE) if "private" in issue.message}
    assert {"actions.pick.when[0]", "actions.probe.when[0]", "actions.count.when[0]"} <= warned
    assert "actions.mine.when[0]" not in warned  # an agent may test its own


LEAKY_POST = {
    "name": "Leaky post",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"secret": {"type": "int", "default": 0, "private": True}}}},
    "entities": {"a": {"type": "p", "props": {"secret": 111}}, "b": {"type": "p", "props": {"secret": 222}},
                 "c": {"type": "p", "props": {"secret": 333}}},
    "records": {"log": {"fields": {"text": "text"}}},
    "actions": {"poke": {"by": "p", "params": {"who": {"type": "entity", "of": "p", "where": "$it.id != $actor.id"}},
                         "do": [{"post": "log", "text": "{$params.who.name} holds {$params.who.secret}"}]}},
    "stages": [{"name": "s", "actions": ["poke"]}],
}


def test_check_refuses_a_post_everyone_reads_that_carries_a_private_property():
    issues = [i for i in fg_env.check(LEAKY_POST) if i.path == "actions.poke.do[0].text"]
    assert issues and issues[0].severity == "error" and "private" in issues[0].message


def test_the_engine_refuses_the_post_so_no_other_agent_ever_reads_it():
    seen = []

    def poke(wake):
        wake.call("poke", {"who": "b"})

    def watch(wake):
        seen.append(wake.update)

    unchecked = copy.deepcopy(LEAKY_POST)  # a read check cannot pin to one agent: the engine still refuses it
    unchecked["actions"]["poke"]["do"][0]["text"] = "{$params.who.name} holds {$get($params.who, secret)}"
    result = fg_env.run(unchecked, {"a": poke, "b": "idle", "c": watch}, seed=1)
    assert not any("222" in text for text in seen)
    [refused] = [d for d in result.diagnostics if d["code"] == "action_rule_failed"]
    assert result.stats["faulted_actions"] == 1 and "private" in refused["message"]


def test_a_post_only_its_owner_reads_may_carry_its_private_property():
    own = copy.deepcopy(LEAKY_POST)
    own["records"]["log"]["visible"] = "$it.author == $viewer.id"
    own["actions"]["poke"] = {"by": "p", "do": [{"post": "log", "text": "mine is {$actor.secret}"}]}
    assert not [i for i in fg_env.check(own) if "private" in i.message]
    directed = copy.deepcopy(LEAKY_POST)
    directed["actions"]["poke"] = {"by": "p",
                                   "do": [{"post": "log", "to": "$actor", "text": "mine is {$actor.secret}"}]}
    assert not [i for i in fg_env.check(directed) if "private" in i.message]
    result = fg_env.run(directed, lambda wake: wake.call("poke"), seed=1)
    assert result.stats["faulted_actions"] == 0 and result.stats["actions"] == 3


BALLOT = {
    "name": "Ballot",
    "clock": {"rounds": 1},
    "world": {"yes": 0},
    "types": {"voter": {"agent": True}},
    "entities": {"a": {"type": "voter"}, "b": {"type": "voter"}},
    "actions": {"vote_yes": {"by": "voter", "do": ["$world.yes += 1"]}, "vote_no": {"by": "voter", "do": []}},
    "stages": [{"name": "vote", "turns": "simultaneous", "actions": ["vote_yes", "vote_no"]}],
}


def test_check_warns_that_a_sealed_stage_announces_which_choice_each_agent_made():
    warned = [i for i in fg_env.check(BALLOT) if i.path == "stages[0]" and "announce: false" in i.fix]
    assert warned and "vote_yes" in warned[0].message
    secret = copy.deepcopy(BALLOT)
    for spec in secret["actions"].values():
        spec["announce"] = False
    assert not [i for i in fg_env.check(secret) if i.path == "stages[0]"]


DOORS = {
    "name": "Doors", "clock": {"rounds": 1},
    "world": {"trap": {"default": 2, "private": True}},
    "types": {"p": {"agent": True, "props": {"coins": 10, "moves": 0}}},
    "entities": {"a": {"type": "p"}},
    "stages": [{"name": "s", "max_actions": 2, "valid": [{"expr": "$actor.moves == 2", "why": "Open two doors."}]}],
    "actions": {"open": {"by": "p", "description": "Open a door; one of 1-3 hides a trap costing 5.",
                         "params": {"n": {"type": "int", "min": 1, "max": 3}},
                         "do": ["$actor.moves += 1",
                                {"if": "$params.n == $world.trap", "then": ["$actor.coins -= 5"]}]}},
    "views": {"purse": {"show": "You have {coins} coins.", "look": True}},
    "outputs": {"coins": "$entity(a).coins"},
}


def test_an_atomic_turn_cannot_read_a_hidden_value_and_then_undo_it_for_free():
    """An action that read a value hidden from its actor settles an atomic turn at once, like one that drew luck:
    undoing the turn afterwards would hand the agent what it learned for free."""
    probes = []

    def prober(wake):
        for door in (1, 2, 3):
            opened = wake.call("open", {"n": door})
            probes.append((opened, wake.call("look", {"view": "purse"}).text))
            if wake.done or opened.ended:
                return
            wake.call("end_turn", {})  # one move breaks `valid`: the turn is undone
            if wake.done:
                return

    result = fg_env.run(DOORS, prober, seed=1)
    opened, _ = probes[0]
    assert len(probes) == 1  # the first door settled the turn: it was undone and the turn is over
    assert not opened.ok and opened.ended and "hidden" in opened.text
    assert result.outputs["coins"] == 10


# -- the one rule, probed across every shape a refusal or an undo can take ------------------------------------------

def _shape(do=None, *, when=None, valid=None, code=None, world=False):
    """A contract in which agent a probes b's hidden `code` (or the world's) with an argument ``n``, by one shape of
    rule: ``do``, a `when`, or a stage `valid`."""
    code_spec = {"type": "int", "default": 0, "private": True, "min": 0, "max": 100, **(code or {})}
    contract = {
        "name": "Probe", "clock": {"rounds": 1},
        "types": {"p": {"agent": True, "props": {"guess": 0, "code": code_spec,
                                                  "slots": {"type": "list", "default": [], "private": True}}}},
        "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
        "actions": {"probe": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": 100}},
                              "do": do or "$actor.guess = $params.n", **({"when": when} if when else {})}},
        "outputs": {"guess": "$entity(a).guess"},
    }
    if world:
        contract["world"] = {"code": dict(code_spec)}
    if valid:
        contract["stages"] = [{"name": "play", "max_calls": 12, "valid": [{"expr": valid, "why": "Not allowed."}]}]
    return contract


PROBE_SHAPES = {
    "a write past a hidden number's min": _shape("$entity(b).code -= $params.n"),
    "a write past a hidden number's max": _shape("$entity(b).code += $params.n"),
    "a hidden world number past its min": _shape("$world.code -= $params.n", world=True),
    "an element update past a hidden list's end": _shape("$entity(b).slots[$params.n] = 1"),
    "a fail in do": _shape({"if": "$params.n > $entity(b).code", "then": [{"fail": "Too high."}]}),
    "a transfer": _shape({"transfer": "code", "from": "$entity(b)", "to": "$actor", "amount": "$params.n"}),
    "a when requirement": _shape(when=[{"expr": "$params.n <= $entity(b).code", "why": "Too high."}]),
    "a stage valid rule": _shape(valid="$entity(b).code >= $actor.guess"),
}


def _probe_run(contract, hidden):
    """The results agent a is given, calling `probe` with a falling argument and ending its turn after each call that
    applies, while b's (and the world's) hidden code is ``hidden``."""
    contract = copy.deepcopy(contract)
    contract["entities"]["b"]["props"] = {"code": hidden, "slots": [0] * hidden}
    if "world" in contract:
        contract["world"]["code"]["default"] = hidden
    seen = []

    def prober(wake):
        for n in (90, 70, 50, 30, 10):
            result = wake.call("probe", {"n": n})
            if result.ok and not result.ended:
                result = wake.end()
            seen.append(result)
            if wake.done or result.ended:
                return

    fg_env.run(contract, {"a": prober, "b": "idle"}, seed=1)
    return seen


def _free(result):
    return not result.ok and not result.ended and not result.data.get("spent")


@pytest.mark.parametrize("shape", list(PROBE_SHAPES))
def test_no_free_refusal_or_undo_turns_on_a_hidden_value_whatever_shape_the_rule_takes(shape):
    """The kernel's rule, probed generically: whatever the shape of the rule that refuses or undoes a call, a refusal
    the agent may retry for free reads the same whatever the hidden value is. Two runs differ only in b's hidden code;
    up to the first call either run pays for, both must have been refused alike."""
    low, high = _probe_run(PROBE_SHAPES[shape], 20), _probe_run(PROBE_SHAPES[shape], 80)
    for one, other in zip(low, high):
        if not (_free(one) or _free(other)):
            break
        assert _free(one) and _free(other) and one.text == other.text, (shape, one.text, other.text)


def _bidders(**parts):
    contract = {"name": "Bidders", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"value": {"type": "number", "default": 1, "private": True},
                                                          "bid": 0}}},
                "entities": {"p": {"type": "p", "count": 3}},
                "actions": {"bid": {"by": "p", "params": {"x": {"type": "int", "min": 0, "max": 9}},
                                    "do": "$actor.bid = $params.x"}},
                "stages": [{"name": "bidding"}]}
    contract.update(parts)
    return contract


@pytest.mark.parametrize("contract, path", [
    (_bidders(stages=[{"name": "bidding", "when": "$entity(p_2).value > 1.5"}]), "stages[0].when"),
    (_bidders(stages=[{"name": "bidding", "until": "$entity(p_2).value < 1.5", "passes": 3}]), "stages[0].until"),
    (_bidders(events=[{"on": "round.start", "when": "$entity(p_2).value > 1.5", "say": "A rumour goes round."}]),
     "events[0].when"),
    (_bidders(events=[{"on": "round.start", "do": [{"if": "$entity(p_2).value > 1.5",
                                                    "then": [{"emit": "hint", "say": "A big spender is here."}]}]}]),
     "events[0].do[0].if"),
])
def test_every_condition_that_decides_what_everyone_sees_by_a_hidden_value_is_warned_alike(contract, path):
    """A stage held or not, the passes it plays, an event's news or a branch that sends everyone news: each tells
    every agent a bit of the hidden value it reads, and `check` says so in the same words wherever it is written."""
    found = [i for i in fg_env.check(contract, rounds=0) if i.path == path]
    assert [i.severity for i in found] == ["warning"] and "and every agent learns" in found[0].message
