"""Whose an entity is, is a stated fact: its type names the property holding its owner, and only the entity itself,
its owner and the agent types a property's `private` lists read that property. A `where` widens nothing."""
import copy

import pytest

import fg_env


def _errors(contract):
    return [issue for issue in fg_env.check(contract) if issue.severity == "error"]


def _vaults(where, owner=True):
    types = {"player": {"agent": True, "props": {}},
             "vault": {"props": {"holder": "", "secret": {"type": "int", "default": 0, "private": True}}}}
    if owner:
        types["vault"]["owner"] = "holder"
    return {"name": "Vaults", "clock": {"rounds": 1}, "types": types,
            "entities": {"ann": {"type": "player"}, "bob": {"type": "player"},
                         "v1": {"type": "vault", "props": {"holder": "ann", "secret": 111}},
                         "v2": {"type": "vault", "props": {"holder": "bob", "secret": 222}}},
            "actions": {"wait": {"by": "player", "do": []}},
            "views": {"vaults": {"for": "player", "of": "vault", "where": where, "show": "{holder}: {secret}"}}}


def test_a_where_naming_the_reader_grants_nothing_without_a_stated_owner():
    for where in ("$it.holder == $actor.id", "$it.holder != $actor.id", "$actor.alive and $it.secret > 0"):
        contract = _vaults(where, owner=False)
        errors = _errors(contract)
        assert [issue.path for issue in errors] == ["views.vaults.show"], where
        # the fix names the property the `where` picks by, when it picks by one
        assert ('"owner": "holder"' if "holder" in where else '"owner": "<') in errors[0].fix
        with pytest.raises(fg_env.ContractError):
            fg_env.load(contract)


def test_the_owner_reads_its_own_and_no_where_widens_it_to_anothers():
    mine = fg_env.load(_vaults("$it.holder == $actor.id"), seed=1)
    assert "ann: 111" in mine.preview("ann")["update"] and "222" not in mine.preview("ann")["update"]
    for where in ("$it.holder != $actor.id", "$it.holder == $actor.id or true", "true", "$it.secret > 0"):
        contract = _vaults(where)
        assert any("v2's secret is private" in issue.message or "private secret of" in issue.message
                   for issue in _errors(contract)), where
        env = fg_env.Env(fg_env.checks.parse_contract(contract), {}, 1)
        with pytest.raises(fg_env.RunError, match="v2's secret is private"):
            env.preview("ann")


def test_a_choice_filtered_by_a_private_value_lists_nothing_it_does_not_own():
    contract = _vaults("$it.holder == $actor.id")
    contract["actions"]["flag"] = {"by": "player", "do": [], "params": {
        "v": {"type": "entity", "of": "vault", "where": "$actor.alive and $it.secret > 150"}}}
    env = fg_env.Env(fg_env.checks.parse_contract(contract), {}, 1)
    with pytest.raises(fg_env.RunError, match="secret is private"):
        env.preview("bob")  # never a list of exactly the vaults whose hidden secret passes
    contract["actions"]["flag"]["params"]["v"]["where"] = "$it.holder == $actor.id and $it.secret > 150"
    tools = fg_env.load(contract, seed=1).preview("bob")["tools"]
    assert next(t for t in tools if t["name"] == "flag")["input_schema"]["properties"]["v"]["enum"] == ["v2"]


def test_ownership_is_read_from_the_value_each_time_and_may_name_several():
    contract = _vaults("$actor.id in $it.holder")
    contract["types"]["vault"]["props"]["holder"] = {"type": "list", "default": []}
    contract["entities"]["v1"]["props"]["holder"] = ["ann"]
    contract["entities"]["v2"]["props"]["holder"] = ["ann", "bob"]
    env = fg_env.load(contract, seed=1)
    assert "111" in env.preview("ann")["update"] and "222" in env.preview("ann")["update"]
    assert "222" in env.preview("bob")["update"] and "111" not in env.preview("bob")["update"]
    env.world.entities["v1"].properties["holder"] = ["bob"]  # handing the vault over hands over what it may read
    assert "111" in env.preview("bob")["update"] and "111" not in env.preview("ann")["update"]


def _review():
    return {"name": "Review", "clock": {"rounds": 1},
            "types": {"author": {"agent": True}, "reviewer": {"agent": True}, "chair": {"agent": True},
                      "review": {"owner": "reviewer",
                                 "props": {"reviewer": "", "paper_author": "",
                                           "score": {"type": "int", "default": 0, "private": ["chair"]}}}},
            "entities": {"au": {"type": "author"}, "rv": {"type": "reviewer"}, "ch": {"type": "chair"},
                         "r1": {"type": "review", "props": {"reviewer": "rv", "paper_author": "au", "score": 3}}},
            "actions": {"wait": {"by": ["author", "reviewer", "chair"], "do": []}},
            "views": {"scored": {"for": "reviewer", "of": "review", "where": "$it.reviewer == $actor.id",
                                 "show": "your score {score}"},
                      "all": {"for": "chair", "of": "review", "show": "score {score}"}}}


def test_a_chair_only_score_reaches_the_chair_and_the_reviewer_but_never_the_papers_author():
    contract = _review()
    env = fg_env.load(contract, seed=1)
    assert "your score 3" in env.preview("rv")["update"] and "score 3" in env.preview("ch")["update"]
    leak = copy.deepcopy(contract)
    leak["views"]["mine"] = {"for": "author", "of": "review", "where": "$it.paper_author == $actor.id",
                             "show": "score {score}"}
    assert any("private score of every review" in issue.message for issue in _errors(leak))
    stated = copy.deepcopy(leak)  # unless the contract makes the paper's author the review's owner
    stated["types"]["review"]["owner"] = "paper_author"
    del stated["views"]["scored"]  # the reviewer owns it no longer: its view would read another's (audit 14 M1)
    assert "score 3" in fg_env.load(stated, seed=1).preview("au")["update"]


def test_an_owner_must_name_a_public_property_of_the_type_and_is_inherited():
    contract = _vaults("$it.holder == $actor.id")
    contract["types"]["vault"]["owner"] = "holdr"
    assert any(i.path == "types.vault.owner" for i in _errors(contract))
    contract["types"]["vault"]["owner"] = "holder"
    contract["types"]["vault"]["props"]["holder"] = {"type": "text", "default": "", "private": True}
    assert any(i.path == "types.vault.owner" and "private" in i.message for i in _errors(contract))
    sub = _vaults("$it.holder == $actor.id")
    sub["types"]["safe"] = {"extends": "vault"}
    sub["entities"]["v1"]["type"] = "safe"
    assert "ann: 111" in fg_env.load(sub, seed=1).preview("ann")["update"]
