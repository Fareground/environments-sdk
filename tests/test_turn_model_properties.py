"""The turn model, checked on many random runs: agents join, leave and reorder mid-round across sequential and
simultaneous stages, and every agent seated when a stage starts gets exactly its turn — unless it
was removed before its turn came."""
import random
import threading
from collections import Counter

import pytest

import fg_env

TURNS = ("sequential", "simultaneous")
ORDERS = (None, "random", "$it.rank")


def _contract(rng):
    """A random table: 2–5 players, 2–5 rounds, 1–2 stages."""
    kinds = [rng.choice(TURNS) for _ in range(rng.randint(1, 2))]
    contract = {
        "name": "Turn model",
        "types": {"player": {"agent": True, "props": {"rank": 0, "seated": 0}}},
        "entities": {f"p{i}": {"type": "player", "props": {"rank": rng.randint(0, 3)}}
                     for i in range(rng.randint(2, 5))},
        "actions": {
            "tick": {"by": "player", "do": []},
            "kill": {"by": "player",
                     "params": {"t": {"type": "entity", "of": "player", "where": "$it.id != $actor.id"}},
                     "do": {"remove": "$params.t"}},
            "spawn": {"by": "player",
                      "params": {"k": {"type": "int", "min": 0}, "rank": {"type": "int", "min": 0, "max": 3}},
                      "do": {"create": "player", "id": "n{$params.k}", "props": {"rank": "$params.rank"}}},
        },
        "stages": [{"name": f"{kind}{k}", "turns": kind, "order": rng.choice(ORDERS)} for k, kind in enumerate(kinds)],
        "events": [{"on": f"stage.{kind}{k}.start", "do": [{"each": "player", "do": ["$it.seated += 1"]}]}
                   for k, kind in enumerate(kinds)],
        "end": [{"when": f"$round >= {rng.randint(2, 5)}"}],
    }
    for stage in contract["stages"]:
        if stage["order"] is None:
            del stage["order"]
    return contract


class _Table:
    """Plays random moves and keeps the book: who took a turn in which stage, and who lost one to removal."""

    def __init__(self, seed, contract):
        self.seed, self.lock = seed, threading.Lock()
        self.known = list(contract["entities"])
        self.took: Counter = Counter()        # (round, stage, agent) -> turns
        self.made = set()                      # (round, stage, agent) created mid-stage: not seated there
        self.lost: Counter = Counter()         # agent -> seated turns it never got (removed before its turn)
        self.sealed = {stage["name"] for stage in contract["stages"] if stage["turns"] == "simultaneous"}

    def __call__(self, wake):
        where = (wake.round, wake.stage)
        rng = random.Random(f"{self.seed}/{where}/{wake.entity_id}")
        with self.lock:
            self.took[(*where, wake.entity_id)] += 1
            move = rng.choices(("tick", "kill", "spawn"), (5, 2, 2))[0]
            if move == "spawn":
                args = {"k": len(self.known), "rank": rng.randint(0, 3)}
                self.known.append(f"n{args['k']}")
            elif move == "kill":
                args = {"t": rng.choice([a for a in self.known if a != wake.entity_id])}
            else:
                args = {}
        outcome = wake.call(move, args)
        if not outcome.ok or wake.stage in self.sealed:
            return  # a sealed choice lands after everyone has taken their turn
        with self.lock:
            if move == "spawn":
                self.made.add((*where, f"n{args['k']}"))
            elif move == "kill" and not self.took[(*where, args["t"])] and (*where, args["t"]) not in self.made:
                self.lost[args["t"]] += 1


@pytest.mark.parametrize("seed", range(60))
def test_every_seated_agent_gets_exactly_its_turns(seed):
    rng = random.Random(seed)
    contract = _contract(rng)
    table = _Table(seed, contract)
    env = fg_env.load(contract, seed=seed)
    result = env.run(table)
    assert result.ok, result.error
    assert max(table.took.values()) == 1, "an agent took two turns in one stage"
    for agent in env.entities("player", alive=False):
        turns = sum(n for (_, _, who), n in table.took.items() if who == agent["id"])
        assert turns == agent["props"]["seated"] - table.lost[agent["id"]], (agent["id"], contract["stages"])


def test_a_removed_agent_does_not_cost_the_agents_after_it_their_turns():
    contract = {
        "name": "Removal",
        "types": {"player": {"agent": True}},
        "entities": {name: {"type": "player"} for name in "abcde"},
        "actions": {"kill": {"by": "player", "params": {"t": {"type": "entity", "of": "player"}},
                             "do": {"remove": "$params.t"}},
                    "tick": {"by": "player", "do": []}},
        "stages": [{"name": "play"}],
        "clock": {"rounds": 1},
    }
    took = []

    def play(wake):
        took.append(wake.entity_id)
        wake.call("kill", {"t": "b"}) if wake.entity_id == "a" else wake.call("tick")

    assert fg_env.run(contract, play, seed=1).ok
    assert took == ["a", "c", "d", "e"]
