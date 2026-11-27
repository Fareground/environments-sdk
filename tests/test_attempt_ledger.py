"""A turn's accounting (runtime/ledger.py): the one decision of what a refused attempt costs, the version of `$pending`
that caches key on, and an atomic part's undo, which keeps a spent attempt spent."""
import fg_env
from fg_env.runtime.ledger import AttemptLedger, Pending, attempt_cost
from fg_env.world.randomness import Context, Observation

GAME = {
    "name": "Ledger",
    "clock": {"rounds": 1},
    "world": {"n": 0},
    "types": {"p": {"agent": True}},
    "entities": {"a": {"type": "p"}},
    "actions": {"add": {"by": "p", "do": ["$world.n += 1"]}},
    "stages": [{"name": "play", "max_actions": 3}],
}


def _observed(draws: int = 0, hidden: int = 0) -> Observation:
    context = Context()
    observed = Observation(context)
    context.draws, context.hidden = draws, hidden
    return observed


def test_a_refusal_is_spent_exactly_when_it_drew_or_read_something_hidden():
    assert attempt_cost(_observed()) == "free"
    assert attempt_cost(_observed(draws=1)) == "spent"
    assert attempt_cost(_observed(hidden=1)) == "spent"


def test_two_pending_lists_never_share_a_version_and_cutting_one_back_restores_its_own():
    first, second = Pending(), Pending()
    assert first.version != second.version
    empty = first.version
    first.append({"action": "buy"})
    second.append({"action": "sell"})
    assert first.version != second.version != empty  # the same length is not the same list
    one = first.version
    first.append({"action": "buy"})
    first.truncate(1)
    assert first.version == one and first.items == [{"action": "buy"}]
    first.truncate(0)
    assert first.version == empty and not first.items


def test_undoing_a_part_takes_back_its_actions_but_not_a_spent_attempt():
    env = fg_env.load(GAME, seed=1)
    ledger = AttemptLedger(env.world, "a", max_actions=3, max_calls=10, atomic=True)
    ledger.pending.append({"action": "add"})
    ledger.took("add")
    assert ledger.refused("add", _observed(hidden=1))  # spent: a use of the action for good
    assert not ledger.refused("add", _observed())  # free: nothing
    assert (ledger.actions_left, ledger.used, ledger.applied) == (1, {"add": 2}, 1)
    assert ledger.undo_part() == 1
    assert (ledger.actions_left, ledger.used, ledger.applied, ledger.pending.items) == (2, {"add": 1}, 0, [])
    assert env.world.used_round["a"] == {"add": 1}


CAPPED = {
    "name": "Capped",
    "clock": {"rounds": 1},
    "world": {"offer": 0},
    "types": {"p": {"agent": True, "props": {"limit": {"type": "number", "default": 50, "private": True}}}},
    "entities": {"a": {"type": "p"}},
    "actions": {"offer": {"by": "p", "params": {"price": {"type": "number", "min": 1, "max": 1000}},
                          "when": [{"expr": "$params.price <= $actor.limit", "why": "That is above your own limit."}],
                          "do": ["$world.offer = $params.price"]}},
    "stages": [{"name": "play", "actions": ["offer"], "max_actions": 1, "max_calls": 3}],
}


def test_a_refusal_by_the_agents_own_private_limit_gives_its_call_back_up_to_a_backstop():
    # The agent cannot see where its owner's limit lies until it meets it: those tries do not use up its turn.
    seen = []

    def play(wake):
        seen.extend(wake.call("offer", {"price": price}) for price in (900, 400, 200, 100, 80))
        seen.append(wake.call("offer", {"price": 50}))
        wake.end()

    fg_env.run(CAPPED, play, seed=1)
    assert [result.ok for result in seen] == [False] * 5 + [True]
    assert all("this try did not count" in result.text for result in seen[:3])
    # Past `max_calls` of them a refusal counts again, so an agent that never learns still runs out of calls.
    seen.clear()

    def stubborn(wake):
        seen.extend(wake.call("offer", {"price": 900}) for _ in range(12))
        wake.end()

    fg_env.run(CAPPED, stubborn, seed=1)
    counted = [result for result in seen if "this try did not count" not in result.text]
    assert sum("this try did not count" in result.text for result in seen) == 3
    assert "your turn is over" in counted[2].text.lower() and counted[2].ended
    # A refusal for any other reason (here, past the stated range, which reads nothing private) counts as before.
    seen.clear()

    def careless(wake):
        seen.extend(wake.call("offer", {"price": 2000}) for _ in range(3))
        wake.end()

    fg_env.run(CAPPED, careless, seed=1)
    assert not any("this try did not count" in result.text for result in seen) and seen[2].ended
    ledger = AttemptLedger(fg_env.load(CAPPED, seed=1).world, "a", max_actions=1, max_calls=1, atomic=False)
    assert ledger.give_back_call() and ledger.calls_left == 2 and not ledger.give_back_call()
