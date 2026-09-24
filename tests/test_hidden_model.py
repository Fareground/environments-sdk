"""One hidden-information model, enforced everywhere an agent looks.

A property declared `private` is hidden from every agent but its owner: an agent owns its own properties; the world's
and any other entity's are hidden from every agent unless a `where` picks the items by the reader
(`$it.owner == $actor.id`). Every channel an agent reads refuses the rest, and any refusal whose evaluation read a value
hidden from the actor spends the action, so a hidden value cannot be probed for free.
"""
import copy

import pytest

import fg_env


def _errors(contract):
    return {issue.path: issue for issue in fg_env.check(contract) if issue.severity == "error"}


def _bisect(tool, lo, hi, param="g"):
    """A participant that binary-searches ``tool``'s ``param`` on the refusal texts "high"/"low", logging each call."""
    log = []

    def play(wake):
        low, high = lo, hi
        while low <= high and len(log) < 20:
            g = (low + high) // 2
            result = wake.call(tool, {param: g})
            log.append((g, result.ok, result.data))
            if result.ok or result.ended:
                break
            if "high" in result.text:
                high = g - 1
            else:
                low = g + 1
        wake.end()
    return play, log


GUESS = {
    "name": "Guess",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"score": 0, "secret": {"type": "int", "default": 0, "private": True}}}},
    "entities": {"ann": {"type": "p"}, "bob": {"type": "p", "props": {"secret": 37}}},
    "stages": [{"name": "play", "max_actions": 1}],
    "actions": {"guess": {"by": "p", "params": {"g": {"type": "int", "min": 1, "max": 100}},
                          "when": [{"expr": "$params.g <= $entity(bob).secret", "why": "Too high."},
                                   {"expr": "$params.g >= $entity(bob).secret", "why": "Too low."}],
                          "do": "$actor.score += 1"}},
    "outputs": {"score": "$entity(ann).score"},
}


def test_a_when_requirement_reading_params_and_a_hidden_value_spends_the_action():
    play, log = _bisect("guess", 1, 100)
    fg_env.run(GUESS, {"ann": play, "bob": "idle"}, seed=5)
    assert len(log) == 1 and log[0][2].get("spent") is True  # one probe, and the turn's only action is gone


def test_a_fault_after_reading_a_hidden_value_spends_the_action():
    faulting = copy.deepcopy(GUESS)
    faulting["actions"]["guess"] = {
        "by": "p", "params": {"g": {"type": "int", "min": 1, "max": 100}},
        "do": ["$x = 1 // ($entity(bob).secret - $params.g + $abs($entity(bob).secret - $params.g))",
               "$actor.score += 1"]}
    play, log = _bisect("guess", 1, 100)
    fg_env.run(faulting, {"ann": play, "bob": "idle"}, seed=5)
    assert log[0][2].get("spent") is True


WORLD_CODE = {
    "name": "Code",
    "clock": {"rounds": 1},
    "world": {"code": {"type": "int", "default": 37, "private": True}},
    "types": {"p": {"agent": True, "props": {"won": 0}}},
    "entities": {"ann": {"type": "p"}},
    "stages": [{"name": "play", "max_actions": 1}],
    "actions": {"guess": {"by": "p", "params": {"g": {"type": "int", "min": 1, "max": 100}},
                          "do": [{"if": "$params.g > $world.code", "then": [{"fail": "Too high."}]},
                                 {"if": "$params.g < $world.code", "then": [{"fail": "Too low."}]},
                                 "$actor.won = 1"]}},
    "outputs": {"won": "$entity(ann).won"},
}


def test_a_private_world_property_is_hidden_so_a_refusal_reading_it_is_spent():
    play, log = _bisect("guess", 1, 100)
    fg_env.run(WORLD_CODE, {"ann": play}, seed=2)
    assert len(log) == 1 and log[0][2].get("spent") is True


def test_a_private_world_property_cannot_be_shown_to_agents():
    shown = copy.deepcopy(WORLD_CODE)
    shown["views"] = {"v": {"show": "The code is {$world.code}"}}
    shown["actions"]["guess"]["announce"] = "code is {$world.code}"
    errors = _errors(shown)
    assert {"views.v.show", "actions.guess.announce"} <= set(errors)
    assert "private" in errors["views.v.show"].message
    # the engine refuses it however the read is spelled (a def hides it from the checker's reading)
    around = copy.deepcopy(WORLD_CODE)
    around["defs"] = {"code": {"expr": "$world.code"}}
    around["views"] = {"v": {"show": "The code is {$code()}"}}
    with pytest.raises(fg_env.RunError, match="the world's code is private"):
        fg_env.load(around, seed=1).preview("ann")


VAULT = {
    "name": "Vault",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"score": 0}},
              "vault": {"props": {"code": {"type": "int", "default": 37, "private": True}}}},
    "entities": {"ann": {"type": "p"}, "v": {"type": "vault"}},
    "actions": {"guess": {"by": "p", "params": {"g": {"type": "int", "min": 1, "max": "$entity(v).code"}},
                          "do": "$actor.score += 1"}},
}


def test_a_private_property_of_a_non_agent_entity_cannot_bound_a_tool():
    assert "actions.guess.params.g.max" in _errors(VAULT)
    with pytest.raises(fg_env.RunError, match="private"):
        fg_env.load(VAULT, seed=1).preview("ann")


VAULT_GUESS = {**VAULT, "actions": {"guess": {**WORLD_CODE["actions"]["guess"], "do": [
    {"if": "$params.g < $entity(v).code", "then": [{"fail": "Too low."}]}, "$actor.score += 1"]}}}


CARDS = {
    "name": "Cards",
    "clock": {"rounds": 1},
    "types": {"p": {"agent": True, "props": {"score": 0}},
              "card": {"props": {"face": {"type": "enum", "values": ["ace", "two"], "default": "two",
                                          "private": True},
                                 "holder": {"type": "text", "default": "", "private": True}}}},
    "entities": {"ann": {"type": "p"}, "bob": {"type": "p"},
                 "c1": {"type": "card", "props": {"holder": "ann"}},
                 "c2": {"type": "card", "props": {"face": "ace", "holder": "bob"}},
                 "c3": {"type": "card", "props": {"holder": "bob"}}},
    "actions": {"draw": {"by": "p", "params": {"card": {"type": "entity", "of": "card", "where": "$it.face == ace"}},
                         "do": "$actor.score += 10"}},
}


def test_a_choice_filtered_by_a_non_agent_private_property_is_refused():
    assert "actions.draw.params.card.where" in _errors(CARDS)
    around = copy.deepcopy(CARDS)
    around["defs"] = {"ace": {"args": ["c"], "expr": "$c.face == ace"}}
    around["actions"]["draw"]["params"]["card"]["where"] = "$ace($it)"
    with pytest.raises(fg_env.RunError, match="c1's face is private"):
        fg_env.load(around, seed=1).preview("ann")


def test_a_where_that_picks_the_items_the_reader_owns_shows_it_their_private_properties():
    mine = copy.deepcopy(CARDS)
    mine["actions"]["draw"]["params"]["card"]["where"] = "$it.holder == $actor.id"
    mine["views"] = {"hand": {"of": "card", "where": "$it.holder == $actor.id", "show": "{id}: {face}"}}
    assert not _errors(mine)
    preview = fg_env.load(mine, seed=1).preview("bob")
    assert preview["update"].endswith("Hand:\n- c2: ace\n- c3: two")
    draw = next(tool for tool in preview["tools"] if tool["name"] == "draw")
    assert draw["input_schema"]["properties"]["card"]["enum"] == ["c2", "c3"]


def test_a_where_that_names_no_owner_reveals_nothing():
    leak = {
        "name": "Leak via non-agent private",
        "clock": {"rounds": 2},
        "types": {"seller": {"agent": True, "props": {"reserve": {"default": 7.77, "private": True}, "listed": False}},
                  "buyer": {"agent": True, "props": {}},
                  "item": {"props": {"reserve": {"default": 0, "private": True},
                                     "owner": {"type": "text", "default": ""}}}},
        "entities": {"s": {"type": "seller"}, "b": {"type": "buyer"}},
        "actions": {"list": {"by": "seller", "when": "not $actor.listed",
                             "do": [{"create": "item", "props": {"reserve": "$actor.reserve", "owner": "$actor.id"}},
                                    "$actor.listed = true"]},
                    "wait": {"by": "buyer", "do": []}},
        "views": {"items": {"for": "buyer", "of": "item", "where": "$it.owner != ''",
                            "show": "{id}: reserve {reserve}"}},
    }
    assert "views.items.show" in _errors(leak)
    rivals = copy.deepcopy(GUESS)
    rivals["views"] = {"rivals": {"of": "p", "where": "$it.id != $actor.id", "show": "{name}: {secret}"}}
    # an agent owns only its own properties: no `where` shows another's
    assert any(path.startswith("views.rivals") for path in _errors(rivals))


def test_a_filtered_view_over_a_hidden_value_is_refused():
    filtered = copy.deepcopy(CARDS)
    filtered["actions"]["draw"]["params"]["card"]["where"] = "$it.holder == $actor.id"
    filtered["views"] = {"aces": {"of": "$filter(card, $it.face == ace)", "show": "{id}"}}
    assert set(_errors(filtered)) == {"views.aces.of"}


def test_a_default_that_reads_a_hidden_value_is_reported_by_check():
    defaulted = copy.deepcopy(GUESS)
    defaulted["actions"]["guess"] = {"by": "p", "params": {"g": {"type": "int", "default": "$entity(bob).secret"}},
                                     "do": "$actor.score += $params.g"}
    issues = {issue.path: issue for issue in fg_env.check(defaulted)}
    assert "private" in issues["actions.guess.params.g.default"].message


def test_who_reading_a_hidden_value_while_choices_are_announced_is_refused():
    roles = {
        "name": "Roles",
        "clock": {"rounds": 1},
        "types": {"p": {"agent": True, "props": {"role": {"type": "enum", "values": ["wolf", "sheep"],
                                                          "default": "sheep", "private": True}}}},
        "entities": {"ann": {"type": "p"}, "bob": {"type": "p", "props": {"role": "wolf"}}},
        "stages": [{"name": "night", "who": "$it.role == wolf"}],
        "actions": {"go": {"by": "p", "do": []}},
    }
    assert "stages[0].who" in _errors(roles)
    around = copy.deepcopy(roles)
    around["defs"] = {"wolf": {"args": ["p"], "expr": "$p.role == wolf"}}
    around["stages"][0]["who"] = "$wolf($it)"
    with pytest.raises(fg_env.RunError, match="stages.night.who: ann's role is private"):
        fg_env.run(around, "random", seed=1)
    secret = copy.deepcopy(roles)
    secret["actions"]["go"]["private"] = True  # nobody learns who acted: waking by role reveals nothing
    assert not _errors(secret)
    assert fg_env.run(secret, "random", seed=1).status == "completed"


def test_a_transfer_refused_for_anothers_hidden_amount_is_generic_and_spent():
    pay = {
        "name": "Pay",
        "clock": {"rounds": 1},
        "types": {"p": {"agent": True, "props": {"cash": {"type": "number", "default": 5, "private": True}}}},
        "entities": {"ann": {"type": "p"}, "bob": {"type": "p", "props": {"cash": 37}}},
        "stages": [{"name": "play", "max_actions": 1}],
        "actions": {"charge": {"by": "p", "params": {"g": {"type": "int", "min": 1, "max": 100}},
                               "do": {"transfer": "cash", "from": "$entity(bob)", "to": "$actor",
                                      "amount": "$params.g"}}},
    }
    seen = []

    def play(wake):
        if wake.entity_id == "ann":
            seen.append(wake.call("charge", {"g": 90}))
        wake.end()

    fg_env.run(pay, play, seed=1)
    assert seen[0].data.get("spent") is True
    assert "Bob" not in seen[0].text and "cover" not in seen[0].text


def test_inspect_hides_every_private_property_but_the_agents_own():
    inspectable = copy.deepcopy(CARDS)
    inspectable["types"]["card"]["inspect"] = True
    inspectable["types"]["card"]["props"]["color"] = "red"
    inspectable["actions"]["draw"]["params"]["card"]["where"] = "$it.holder == $actor.id"
    seen = []

    def play(wake):
        if wake.entity_id == "ann":
            seen.append(wake.call("inspect", {"id": "c2"}).text)
        wake.end()

    fg_env.run(inspectable, play, seed=1)
    assert "color: red" in seen[0] and "ace" not in seen[0] and "bob" not in seen[0]


@pytest.mark.parametrize("args", ["deep", {"g": "hi\ud800"}])
def test_deeply_nested_or_unencodable_arguments_are_an_invalid_call_not_a_crashed_run(args, tmp_path):
    if args == "deep":
        deep: list = []
        node = deep
        for _ in range(5000):
            node.append([])
            node = node[0]
        args = {"g": deep}
    contract = {"name": "Bounds", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"said": {"type": "text", "default": ""}}}},
                "entities": {"ann": {"type": "p"}},
                "actions": {"say": {"by": "p", "params": {"g": {"type": "text"}}, "do": "$actor.said = $params.g"}}}
    seen = []

    def play(wake):
        seen.append(wake.call("say", args))
        wake.end()

    result = fg_env.run(contract, play, seed=1)
    assert result.status == "completed"
    assert seen[0].data == {"error": "invalid"} and not seen[0].ok
    result.save(tmp_path / "run.json")  # the run's record stays writable


def test_a_model_replying_with_deeply_nested_json_makes_an_invalid_call_not_a_crashed_run():
    from types import SimpleNamespace as NS

    class FakeOpenAI:
        def __init__(self):
            self.chat = NS(completions=NS(create=self.create))

        def create(self, **_):
            call = NS(id="c1", function=NS(name="bet", arguments='{"amount":' + "[" * 3000 + "]" * 3000 + "}"))
            message = NS(content="", tool_calls=[call], refusal=None)
            return NS(choices=[NS(message=message, finish_reason="tool_calls")],
                      usage=NS(prompt_tokens=1, completion_tokens=1))

    contract = {"name": "C", "clock": {"rounds": 1}, "types": {"player": {"agent": True, "props": {"coins": 10}}},
                "entities": {"ann": {"type": "player"}},
                "actions": {"bet": {"by": "player", "params": {"amount": {"type": "int", "min": 1, "max": 10}},
                                    "do": "$actor.coins -= 0"}}}
    result = fg_env.run(contract, {"ann": fg_env.participants.openai(FakeOpenAI(), "m")}, seed=1)
    assert result.status == "completed"


def test_check_probes_hidden_numbers_and_finds_none_once_refusals_are_spent():
    for contract in (GUESS, WORLD_CODE, VAULT_GUESS):
        assert not [issue for issue in fg_env.check(contract) if "probing agent" in issue.message]


def test_the_probing_agent_reports_a_hidden_number_a_free_refusal_tells(monkeypatch):
    from fg_env.runtime import ledger

    # as if no refusal ever read anything hidden
    monkeypatch.setattr(ledger, "attempt_cost", lambda observed: "spent" if observed.drew else "free")
    for contract, hidden in ((WORLD_CODE, "the world's hidden code"), (GUESS, "bob's hidden secret"),
                             (VAULT_GUESS, "v's hidden code")):
        issues = [issue for issue in fg_env.check(contract) if "probing agent" in issue.message]
        assert [issue.path for issue in issues] == ["actions.guess"] and hidden in issues[0].message


def test_a_payment_refused_for_anothers_hidden_balance_is_generic_and_spent_but_ones_own_is_told():
    ledger = {
        "name": "Ledger", "clock": {"rounds": 1},
        "types": {"person": {"agent": True, "props": {"cash": {"type": "number", "default": 37, "private": True}}}},
        "entities": {"ann": {"type": "person", "name": "Ann"}, "bob": {"type": "person", "name": "Bob"}},
        "mechanisms": {"money": {"kind": "economy", "mode": "ledger", "who": "person", "currencies": {"cash": {}}}},
        "actions": {"charge": {"by": "person", "params": {"g": {"type": "int", "min": 1, "max": 100}},
                               "do": {"economy": "money", "action": "pay", "from": "$entity(bob)", "to": "$actor",
                                      "amount": "$params.g"}},
                    "give": {"by": "person", "params": {"g": {"type": "int", "min": 1, "max": 100}},
                             "do": {"economy": "money", "action": "pay", "from": "$actor", "to": "$entity(bob)",
                                    "amount": "$params.g"}}},
        "stages": [{"name": "play", "actions": ["charge", "give"], "max_actions": 2, "max_calls": 5}],
    }
    seen = []

    def play(wake):
        if wake.entity_id == "ann":
            seen.extend([wake.call("give", {"g": 90}), wake.call("charge", {"g": 90})])
        wake.end()

    fg_env.run(ledger, play, seed=1)
    own, theirs = seen
    assert "Ann has only 37 cash" in own.text and not own.data.get("spent")  # her own balance she may know
    assert theirs.text.startswith("That payment cannot be made.") and theirs.data.get("spent") is True


def test_private_on_a_link_field_is_an_error_not_silently_ignored():
    linked = {**copy.deepcopy(GUESS), "relations": {"trusts": {"props": {"since": {"default": 0, "private": True}}}}}
    assert "relations.trusts.props.since.private" in _errors(linked)


def test_a_fail_text_that_reads_a_hidden_value_is_a_check_error():
    told = copy.deepcopy(WORLD_CODE)
    told["actions"]["guess"]["do"] = [{"if": "$params.g > 1", "then": [{"fail": "The code is {$world.code}."}]}]
    assert "actions.guess.do[0].then[0].fail" in _errors(told)
