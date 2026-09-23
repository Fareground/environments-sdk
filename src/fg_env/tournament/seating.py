"""Who sits where: every seating of a round robin or an all-play-all, and the tables of one Swiss round."""
from __future__ import annotations

from itertools import combinations, permutations
from typing import Dict, FrozenSet, List, Mapping, Sequence, Tuple

__all__ = ["PAIRINGS", "Seating", "rotations", "schedule", "swiss_round"]

PAIRINGS = ("round_robin", "all_play_all", "swiss")

#: The entrant in each seat, in seat order.
Seating = Tuple[str, ...]


def rotations(group: Sequence[str]) -> List[Seating]:
    """The group turned around the table once: every member sits in every seat exactly once."""
    return [tuple(group[r:]) + tuple(group[:r]) for r in range(len(group))]


def schedule(entrants: Sequence[str], seats: int, pairing: str) -> List[Seating]:
    """Every seating of a full tournament.

    ``round_robin``: every group of ``seats`` entrants, rotated through the seats. ``all_play_all``: every
    group in every order, so each entrant also meets every opponent in every relative position (who acts
    just before whom matters in sequential games). For two seats the two are the same.
    """
    groups = list(combinations(entrants, seats))
    if pairing == "round_robin":
        return [seating for group in groups for seating in rotations(group)]
    if pairing == "all_play_all":
        return [seating for group in groups for seating in permutations(group)]
    raise ValueError(f"a fixed schedule is round_robin or all_play_all, got {pairing!r}")


def swiss_round(ranked: Sequence[str], seats: int, met: Mapping[FrozenSet[str], int],
                byes: Mapping[str, int]) -> Tuple[List[Tuple[str, ...]], List[str]]:
    """Tables for one Swiss round and the entrants who sit out.

    ``ranked`` lists entrants best first. When they do not fill whole tables, the lowest-ranked among
    those with the fewest byes sit out. Each table is filled from the top of the ranking with the
    entrants who have met its members least often (ties to the higher-ranked), so rematches happen
    only when every alternative is also a rematch.
    """
    position: Dict[str, int] = {name: i for i, name in enumerate(ranked)}
    extra = len(ranked) % seats
    resting = sorted(ranked, key=lambda n: (byes.get(n, 0), -position[n]))[:extra]
    waiting = [n for n in ranked if n not in resting]
    tables: List[Tuple[str, ...]] = []
    while waiting:
        table = [waiting.pop(0)]
        while len(table) < seats:
            best = min(range(len(waiting)),
                       key=lambda i: (sum(met.get(frozenset((waiting[i], t)), 0) for t in table), i))
            table.append(waiting.pop(best))
        tables.append(tuple(table))
    return tables, sorted(resting, key=position.__getitem__)
