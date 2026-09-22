"""One seed tree per run: every random stream is derived from the run seed and a path.

Streams are independent of each other and of call order across subsystems, so adding
an event with a chance roll never changes how the population was sampled. Arms of an
experiment share the tree for the same run index (common random numbers).

While a run plays, each block of logic draws from the stream of its :class:`DrawSite` — where
the block is written (and whose action it is), the round, and how many times that site has
drawn this round — so no participant's choices shift the world's draws, or another agent's.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, Optional, Union

__all__ = ["SeedTree", "DrawSite", "mint_seed"]

PathPart = Union[str, int]


def mint_seed() -> int:
    """A fresh seed from system entropy (recorded on the run so it can be replayed)."""
    return random.SystemRandom().getrandbits(63)


class SeedTree:
    __slots__ = ("seed",)

    def __init__(self, seed: int):
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError(f"seed must be an integer, got {seed!r}")
        self.seed = seed

    def derive(self, *path: PathPart) -> int:
        digest = hashlib.sha256(repr((self.seed, *path)).encode()).digest()
        return int.from_bytes(digest[:8], "big") >> 1

    def child(self, *path: PathPart) -> "SeedTree":
        return SeedTree(self.derive(*path))

    def rng(self, *path: PathPart) -> random.Random:
        return random.Random(self.derive(*path))

    def __repr__(self) -> str:
        return f"SeedTree({self.seed})"


class DrawSite:
    """The stream of one run of a block of logic, opened at its first draw (most runs of a block draw nothing).

    Opening counts the runs of the site that drew this round in ``world.firings``, journaled: a block that is undone
    (a refused action, a dry run) gives its draws back, so trying again in the same round rolls the same luck."""

    __slots__ = ("key", "stream")

    def __init__(self, key: str):
        self.key = key
        self.stream: Optional[random.Random] = None

    def open(self, world: Any) -> random.Random:
        if self.stream is None:
            count = world.firings.get(self.key, 0)
            world.firings[self.key] = count + 1
            world.journal.push(lambda: self._give_back(world.firings, count))
            self.stream = world.seeds.rng("draws", self.key, world.round, count)
        return self.stream

    def _give_back(self, firings: Dict[str, int], count: int) -> None:
        """Undo the opening; a block that goes on after part of it was undone draws those same numbers again."""
        firings[self.key] = count
        self.stream = None
