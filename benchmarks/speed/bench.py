"""Engine speed on realistic workloads: wall time and microseconds per decision (one agent turn).

    PYTHONPATH=src python benchmarks/speed/bench.py            # every workload, best of 3
    PYTHONPATH=src python benchmarks/speed/bench.py crowd -n 1 # one workload, one repeat

Every workload runs coded participants under a fixed seed, so the numbers compare one engine build with another.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import fg_env
from fg_env.participants import RandomAgent

CONTRACTS = Path(__file__).resolve().parents[2] / "examples" / "contracts"

#: 5,000 coded agents: each turn a policy picks another agent (an entity parameter) and gives it a coin.
CROWD = {
    "name": "Crowd",
    "clock": {"rounds": 4},
    "types": {"citizen": {"agent": True, "policy": "giver", "props": {"coins": 5}}},
    "population": [{"type": "citizen", "count": 5000}],
    "actions": {"give": {"by": "citizen",
                         "params": {"to": {"type": "entity", "of": "citizen", "where": "$it.id != $actor.id"}},
                         "when": "$actor.coins > 0",
                         "do": ["$actor.coins -= 1", "$params.to.coins += 1"]}},
    "policies": {"giver": {"rules": [{"when": "$actor.coins > 2", "do": "give", "with": {"to": "$choice(citizen)"}},
                                     {"do": "pass"}]}},
    "metrics": {"rich": "$count(citizen, $it.coins > 8)"},
    "outputs": {"coins": {"expr": "$sum(citizen, $it.coins)", "type": "int"}},
}

#: 20 players who read their update and the tool schemas each turn, as a model would, then act.
GAME = {
    "name": "Table",
    "clock": {"rounds": 100},
    "types": {"player": {"agent": True, "props": {"chips": 20, "wins": 0}},
              "pot": {"props": {"chips": 0}}},
    "entities": {"pot": {"type": "pot"}},
    "population": [{"type": "player", "count": 20}],
    "actions": {
        "bet": {"by": "player", "params": {"amount": {"type": "int", "min": 1, "max": "$min(5, $actor.chips)"}},
                "when": "$actor.chips > 0",
                "do": ["$actor.chips -= $params.amount", "$entity(pot).chips += $params.amount"],
                "announce": "{$actor.name} bets {$params.amount}."},
        "challenge": {"by": "player", "params": {"rival": {"type": "entity", "of": "player",
                                                           "where": "$it.id != $actor.id"}},
                      "do": [{"if": "$chance(0.5)", "then": ["$actor.wins += 1"], "else": ["$params.rival.wins += 1"]}],
                      "announce": "{$actor.name} challenges {$params.rival.name}."}},
    "events": [{"phase": "end", "do": [{"if": "$entity(pot).chips > 40",
                                        "then": ["$choice(player).chips += $entity(pot).chips",
                                                 "$entity(pot).chips = 0"]}]}],
    "views": {"table": {"title": "The table", "show": "{$count(player, $it.chips > 0)} players still have chips."}},
    "metrics": {"pot": "$entity(pot).chips"},
    "outputs": {"wins": {"expr": "$max(player, $it.wins)", "type": "int"}},
}


class Reader:
    """Reads the update and the provider tool schemas as a model would, then acts at random."""

    concurrent = False

    def __init__(self) -> None:
        self.act = RandomAgent(seed=3)

    def __call__(self, wake: fg_env.Wake) -> None:
        _ = (wake.update, wake.tools_for("anthropic"))
        self.act(wake)


#: name -> (contract, load options, participants)
WORKLOADS: Dict[str, Tuple[Any, Dict[str, Any], Any]] = {
    "exchange": (CONTRACTS / "exchange_flagship.json", {"inputs": {"bars": 20}}, None),
    "coffee_market": (CONTRACTS / "coffee_market.json", {}, None),
    "outbreak_network": (CONTRACTS / "outbreak_network.json", {}, None),
    "crowd": (CROWD, {}, None),
    "game20": (GAME, {}, Reader()),
}


def measure(name: str, repeats: int) -> Dict[str, Any]:
    contract, options, participants = WORKLOADS[name]
    best = float("inf")
    result: Any = None
    for _ in range(repeats):
        env = fg_env.load(contract, seed=1, **options)
        start = time.perf_counter()
        result = env.run(participants)
        best = min(best, time.perf_counter() - start)
    assert result.status in ("completed", "ended"), f"{name}: {result.status} {result.error}"
    decisions = result.stats["wakes"]
    return {"name": name, "seconds": best, "rounds": result.rounds, "decisions": decisions,
            "us_per_decision": best / decisions * 1e6 if decisions else float("nan")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workloads", nargs="*", metavar="workload", help=f"any of: {', '.join(WORKLOADS)} (default: all)")
    parser.add_argument("-n", "--repeats", type=int, default=3)
    args = parser.parse_args()
    unknown = sorted(set(args.workloads) - set(WORKLOADS))
    if unknown:
        parser.error(f"unknown workload {', '.join(unknown)}: use any of {', '.join(WORKLOADS)}")
    print(f"{'workload':<18}{'seconds':>9}{'rounds':>8}{'decisions':>11}{'µs/decision':>13}")
    for name in args.workloads or WORKLOADS:
        row = measure(name, args.repeats)
        print(f"{row['name']:<18}{row['seconds']:>9.2f}{row['rounds']:>8}{row['decisions']:>11}"
              f"{row['us_per_decision']:>13.0f}", flush=True)


if __name__ == "__main__":
    main()
