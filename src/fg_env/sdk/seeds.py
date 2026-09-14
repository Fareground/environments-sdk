"""One seed tree per run: every random stream is derived from the run seed and a path.

Streams are independent of each other and of call order across subsystems, so adding
an event with a chance roll never changes how the population was sampled. Arms of an
experiment share the tree for the same run index (common random numbers).
"""
from __future__ import annotations

import hashlib
import random
from typing import Union

__all__ = ["SeedTree", "mint_seed"]

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
