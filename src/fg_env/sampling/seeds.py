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
from collections.abc import Callable
from typing import Any

__all__ = ["SeedTree", "DrawSite", "LazyStream", "mint_seed", "copy_stream"]

PathPart = str | int


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

    def child(self, *path: PathPart) -> SeedTree:
        return SeedTree(self.derive(*path))

    def rng(self, *path: PathPart) -> random.Random:
        return random.Random(self.derive(*path))

    def lazy_rng(self, *path: PathPart) -> LazyStream:
        """:meth:`rng`, seeded at its first use."""
        return LazyStream(lambda: self.rng(*path))

    def __repr__(self) -> str:
        return f"SeedTree({self.seed})"


class LazyStream:
    """A random stream made by ``make`` at its first use: seeding one costs more than most of its users ever draw
    (a turn's own stream, a coded policy's), and most never draw at all."""

    __slots__ = ("_make", "_stream")

    def __init__(self, make: Callable[[], random.Random]):
        self._make: Callable[[], random.Random] | None = make
        self._stream: random.Random | None = None

    def __getattr__(self, name: str) -> Any:
        if self._stream is None:
            assert self._make is not None
            self._stream, self._make = self._make(), None
        return getattr(self._stream, name)

    def copy(self) -> LazyStream:
        """The same stream, drawing on apart from this one (still unseeded if this one is)."""
        copied = LazyStream.__new__(LazyStream)
        copied._make, copied._stream = self._make, None if self._stream is None else copy_stream(self._stream)
        return copied


def copy_stream(stream: Any) -> Any:
    """A copy of a random stream (a :class:`random.Random` or a :class:`LazyStream`) that draws on apart from it."""
    if isinstance(stream, LazyStream):
        return stream.copy()
    copied = random.Random.__new__(random.Random)
    copied.setstate(stream.getstate())
    return copied


class DrawSite:
    """The stream of one run of a block of logic, opened at its first draw (most runs of a block draw nothing).

    Opening counts the runs of the site that drew this round in the run's ``firings`` (see
    :mod:`~fg_env.world.randomness`). Nothing gives a count back: a block that is undone after it drew (a refused
    action, an undone turn) has spent its luck, so trying again rolls afresh and no refusal lets an agent probe its
    luck. Trials draw nothing (see ``ActionBook.trying``)."""

    __slots__ = ("key", "stream")

    def __init__(self, key: str):
        self.key = key
        self.stream: random.Random | None = None

    def open(self, luck: Any, round: int) -> random.Random:
        """The stream, opened at its first draw in ``round`` from the run's ``luck`` (a ``Randomness``)."""
        if self.stream is None:
            count = luck.firings.get(self.key, 0)
            luck.firings[self.key] = count + 1
            self.stream = luck.seeds.rng("draws", self.key, round, count)
        return self.stream
