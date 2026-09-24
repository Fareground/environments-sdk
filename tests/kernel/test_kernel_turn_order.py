"""Turn-order fairness: in every pass of every stage each woken agent gets exactly one turn, a stage takes as many
passes as its `passes` and `until` say, and the sealed choices of a simultaneous pass commit once each — in turn order
when the stage sets an `order`, else in a random order drawn for the pass.

Random tables of 2–5 players play 1–2 stages, sequential or simultaneous, of 1–3 passes, some repeating `until` a
count is reached, ordered by default, at random or by a property. Every agent always acts (``tick``), and each tick
posts who made it, so the record shows the order choices committed in. Agents are never added or removed here: that
is ``tests/test_turn_model_properties.py``.
"""
import math
import random
from collections import defaultdict

import pytest

import fg_env

TABLES_FAST = range(12)
TABLES_SLOW = range(12, 300)


def _table(seed):
    rng = random.Random(seed)
    players = rng.randint(2, 5)
    stages = []
    for k in range(rng.randint(1, 2)):
        stage = {"name": f"s{k}", "turns": rng.choice(["sequential", "simultaneous"]), "passes": rng.randint(1, 3)}
        order = rng.choice([None, "random", "$it.rank"])
        if order is not None:
            stage["order"] = order
        if rng.random() < 0.4:
            stage["until"] = f"$world.ticks >= {rng.randint(1, 4 * players)}"
        stages.append(stage)
    return {
        "name": "Turn order",
        "clock": {"rounds": rng.randint(2, 4)},
        "world": {"ticks": 0},
        "types": {"player": {"agent": True, "props": {"rank": 0}}},
        "entities": {f"p{i}": {"type": "player", "props": {"rank": rng.randint(0, 2)}} for i in range(players)},
        "records": {"commits": {"fields": {"who": "text"}}},
        "actions": {"tick": {"by": "player", "do": ["$world.ticks += 1", {"post": "commits", "who": "$actor.id"}]}},
        "stages": stages,
    }


def _passes_expected(stage, ticks_before, players):
    """How many passes the stage takes, given the count when it starts (every turn ticks once)."""
    if "until" not in stage:
        return stage["passes"]
    target = int(stage["until"].rsplit(" ", 1)[1])
    needed = max(1, math.ceil((target - ticks_before) / players))
    return min(stage["passes"], needed)


def _chunks(items, size):
    return [items[k:k + size] for k in range(0, len(items), size)]


def _fair(seed):
    contract = _table(seed)
    players = sorted(contract["entities"])
    env = fg_env.load(contract, seed=seed)
    woken, ticks_at = defaultdict(list), {}

    def play(wake):
        where = (wake.round, wake.stage)
        ticks_at.setdefault(where, env.props["ticks"])
        woken[where].append(wake.entity_id)
        assert wake.call("tick").ok

    result = env.run(play)
    assert result.status == "completed", result.error
    committed = defaultdict(list)
    for entry in env.records("commits"):
        committed[(entry["round"], entry["stage"])].append(entry["who"])
    shuffled = []
    for (round_, name), turns in woken.items():
        stage = next(stage for stage in contract["stages"] if stage["name"] == name)
        passes = _chunks(turns, len(players))
        assert all(sorted(one) == players for one in passes), f"round {round_}, {name}: not one turn each {passes}"
        assert len(passes) == _passes_expected(stage, ticks_at[(round_, name)], len(players)), (round_, name, passes)
        commits = _chunks(committed[(round_, name)], len(players))
        assert all(sorted(one) == players for one in commits), f"round {round_}, {name}: commits {commits}"
        if stage["turns"] == "sequential" or "order" in stage:
            assert commits == passes, f"round {round_}, {name}: committed out of turn order"
        else:
            shuffled += [commit != turn for commit, turn in zip(commits, passes)]
    return shuffled


@pytest.mark.parametrize("seed", TABLES_FAST)
def test_every_woken_agent_gets_one_turn_per_pass_and_sealed_choices_commit_once_each(seed):
    _fair(seed)


def test_sealed_choices_without_an_order_commit_in_a_drawn_order():
    shuffled = [moved for seed in TABLES_FAST for moved in _fair(seed)]
    assert any(shuffled) and not all(shuffled)  # a random order: sometimes the turn order, mostly not


@pytest.mark.slow
@pytest.mark.parametrize("seed", TABLES_SLOW)
def test_every_woken_agent_of_many_tables_gets_one_turn_per_pass(seed):
    _fair(seed)
