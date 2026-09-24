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
