"""The `chance` effect: one outcome from a listed distribution, drawn by the seed or chosen by a picker."""
import copy

import pytest

import fg_env

COIN = {
    "name": "Coin flips",
    "clock": {"rounds": 6},
    "world": {"heads": 0, "tails": 0, "last": {"type": "text", "default": ""}},
    "types": {"flipper": {"agent": True, "props": {}}},
    "entities": {"f": {"type": "flipper"}},
    "actions": {"wait": {"by": "flipper", "do": []}},
    "events": [{"phase": "start", "do": [
        {"chance": [{"p": 0.5, "label": "heads", "do": ["$world.heads += 1"]},
                    {"p": 0.5, "label": "tails", "do": ["$world.tails += 1"]}], "as": "side"},
        "$world.last = $side"]}],
    "outputs": {"heads": {"expr": "$world.heads", "type": "int"}},
}

DECK = {
    "name": "Draws",
    "clock": {"rounds": 20},
    "world": {"deck": {"type": "list", "default": ["ace", "bishop", "crown"]},
              "drawn": {"type": "list", "default": []}},
    "types": {"dealer": {"agent": True, "props": {}}},
    "entities": {"d": {"type": "dealer"}},
    "actions": {"wait": {"by": "dealer", "do": []}},
    "events": [{"phase": "start", "do": [
        {"chance": "draw", "outcomes": "$world.deck", "weight": "0 if $it == 'bishop' else 1", "as": "card",
         "do": ["$world.drawn += $card"]}]}],
    "outputs": {"drawn": {"expr": "$world.drawn", "type": "list"}},
}


def _chance_events(result):
    return [event for event in result.events if event["kind"] == "chance"]


def test_a_branch_chance_picks_one_branch_by_the_seed_and_logs_it():
    first, again = fg_env.run(COIN, seed=4), fg_env.run(COIN, seed=4)
    assert first.events == again.events
    picks = _chance_events(first)
    assert len(picks) == 6
    assert all(e["data"]["outcome"] in ("heads", "tails") and e["data"]["p"] == 0.5 and e["to"] == [] for e in picks)
    heads = sum(1 for e in picks if e["data"]["outcome"] == "heads")
    assert first.outputs["heads"] == heads
    env = fg_env.load(COIN, seed=4)
    env.run()
    assert env.props["last"] == picks[-1]["data"]["outcome"]


def test_a_named_chance_picks_an_item_by_weight():
    result = fg_env.run(DECK, seed=2)
    assert result.ok, result.summary()
    assert len(result.outputs["drawn"]) == 20
    assert "bishop" not in result.outputs["drawn"]
    assert {e["data"]["p"] for e in _chance_events(result)} == {0.5}


def _messages(contract):
    return [(issue.path, issue.message) for issue in fg_env.check(contract) if issue.severity == "error"]


def test_the_checker_explains_malformed_chance_effects():
    uneven = copy.deepcopy(COIN)
    uneven["events"][0]["do"][0]["chance"][1]["p"] = 0.4
    assert any("add up to 0.9" in message for _, message in _messages(uneven))
    stray = copy.deepcopy(COIN)
    stray["events"][0]["do"][0]["chance"][0]["weight"] = 2
    assert any("not part of a chance branch" in message for _, message in _messages(stray))
    reserved = copy.deepcopy(COIN)
    reserved["events"][0]["do"][0]["as"] = "actor"
    assert any("built-in root" in message for _, message in _messages(reserved))
    unnamed = copy.deepcopy(DECK)
    del unnamed["events"][0]["do"][0]["outcomes"]
    assert any("needs `outcomes`" in message for _, message in _messages(unnamed))
    mixed = copy.deepcopy(COIN)
    mixed["events"][0]["do"][0]["outcomes"] = [1, 2]
    assert any("does not take `outcomes`" in message for _, message in _messages(mixed))


def test_probabilities_computed_at_run_time_must_add_up():
    computed = copy.deepcopy(COIN)
    computed["world"]["p"] = 0.3
    for branch in computed["events"][0]["do"][0]["chance"]:
        branch["p"] = "$world.p"
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(computed, seed=1)
    result = failed.value.result
    assert result.status == "failed"
    assert "add up to 0.6, not 1" in result.error and "events[0].do[0].chance" in result.error


def test_a_chance_chooser_given_at_load_decides_every_outcome_and_copies_keep_it():
    def always_tails(node):
        return next(outcome.index for outcome in node.outcomes if outcome.label == "tails")

    env = fg_env.load(COIN, seed=4, chance=always_tails)
    env.run(rounds=3)
    copy_of_env = env.clone()
    assert env.run().outputs["heads"] == 0
    assert copy_of_env.run().outputs["heads"] == 0
    with pytest.raises(ValueError, match="fg_env.rl.game"):
        fg_env.load(COIN, chance="explicit")


def test_an_event_fires_at_random_through_its_when():
    contract = copy.deepcopy(COIN)
    contract["events"][0]["chance"] = 0.5
    with pytest.raises(fg_env.ContractError) as info:
        fg_env.parse(contract)
    issue = next(i for i in info.value.issues if i.path == "events[0].chance")
    assert "'chance' is not a field here" in issue.message and '"when": "$chance(0.5)"' in (issue.fix or "")
    contract["events"][0].pop("chance")
    contract["events"][0]["when"] = "$chance(0.5)"
    heads = fg_env.run(contract, seed=2).outputs["heads"]
    assert 0 < heads < 6
