"""A revision that narrows what agents do — one value left for an argument, the play cut to one round — is found by
its test runs like any other gutting, and only the kept revision's behaviour counts (audit 14 agentif MEDIUM-A1)."""
import copy

import pytest

from fg_env.authoring import testing
from fg_env.authoring.behaviour import collapsed

LEMONADE = {
    "name": "Lemonade duel", "clock": {"rounds": 5},
    "brief": {"situation": "Two lemonade stands compete for walkers.", "roles": {"stand": "Maximise your profit."}},
    "types": {"stand": {"agent": True, "props": {"price": 3, "sold": 0, "profit": 0},
                        "score": {"value": "$it.profit"}}},
    "entities": {"ann": {"type": "stand"}, "bob": {"type": "stand"}},
    "actions": {"set_price": {"by": "stand", "description": "Set today's price.",
                              "params": {"price": {"type": "int", "min": 1, "max": 5}},
                              "do": "$actor.price = $params.price", "terminal": True}},
    "events": [{"name": "sales", "on": "round.end", "do": [
        {"each": "stand", "do": ["$it.sold = $max(0, 12 - 2 * $it.price + $randint(0, 3))",
                                 "$it.profit += ($it.price - 1) * $it.sold"]}]}],
    "views": {"prices": {"of": "stand", "show": "{$it.name}: {$it.price} (sold {$it.sold})"}},
    "outputs": {"profit": "$dict(stand, $it.id, $it.profit)", "avg_price": "$avg($map(stand, $it.price))"},
}
PARTS = {"actions.set_price", "outputs.profit", "outputs.avg_price", "views.prices", "events.sales"}


def _narrowed_price(c):
    c["actions"]["set_price"]["params"]["price"]["max"] = 1


def _one_round_of_play(c):
    c["stages"] = [{"name": "play", "when": "$round == 1"}]


@pytest.mark.parametrize("narrow, lost", [(_narrowed_price, "actions.set_price.price"),
                                          (_one_round_of_play, "turns with an action")])
def test_a_revision_that_narrows_what_agents_do_is_found_by_its_runs(narrow, lost):
    before = testing.tested(LEMONADE)
    changed = copy.deepcopy(LEMONADE)
    narrow(changed)
    after = testing.tested(changed)
    assert not before.problem and not after.problem
    found = collapsed(before.profile, after.profile, PARTS)
    assert any(item.startswith(lost) for item in found), found
    assert not collapsed(before.profile, testing.tested(copy.deepcopy(LEMONADE)).profile, PARTS)  # the same plays the same
