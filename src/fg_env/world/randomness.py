"""The run's luck: which random stream the running logic draws from, and what a piece of work drew or read.

The rule of luck lives here, once:

* Every draw comes from the stream of the context that makes it (:meth:`Randomness.current`): the draw site of the
  block of logic running (:meth:`Randomness.at`), else the stream a turn or a view set (:meth:`Randomness.using`), else
  the run's main stream. Contexts are per thread and per asyncio task, so concurrent turns never race for draws.
* A draw site's stream is opened at its first draw and counted in :attr:`Randomness.firings`, which nothing gives back:
  luck drawn stays spent, whatever is undone after it (see :class:`~fg_env.sampling.seeds.DrawSite`).
* Where nothing may be left to luck, a draw is forbidden (:meth:`Randomness.forbidden`).
* :meth:`Randomness.observe` is the only way to ask whether work was pure — whether it drew, or read a value hidden
  from the acting agent (see expr/hidden.py) — and so whether its result may be reused or its refusal is free.

Stream names are seeds: renaming one changes the luck of every contract that draws from it. The names events kept from
what they were written as before are frozen in :data:`LEGACY_STREAMS`.
"""
from __future__ import annotations

import random
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

from ..expr.base import ExprError
from ..expr.objects import Entity
from ..sampling.seeds import DrawSite, LazyStream, PathPart, SeedTree

__all__ = ["Randomness", "Context", "Observation", "LuckAhead", "LEGACY_STREAMS", "event_streams"]


class LuckAhead(BaseException):
    """A trial reached a random draw (see :meth:`Randomness.forbidden`): what follows depends on luck, which only the
    call itself may roll. Not an :class:`Exception`, so no rule or mechanism takes it for a failure of its own."""


class Context:
    """What one thread or asyncio task is running: its random stream, a turn's ``$pending`` (what the turn did or
    submitted so far, with its version) and deadline (``time.monotonic()``), the agent whose action runs and which
    action it is, how deep defs call each other, why draws are forbidden (None: they are not; empty: a trial), and how
    many draws and reads of values hidden from the acting agent it has made (what an :class:`Observation` compares)."""

    __slots__ = ("rng", "pending", "deadline", "actor", "action", "depth", "forbid", "draws", "hidden")

    def __init__(self, rng: Any = None, pending: Any = None, deadline: float | None = None):
        self.rng = rng
        self.pending = pending
        self.deadline = deadline
        self.actor: Entity | None = None
        self.action: str | None = None
        self.depth = 0
        self.forbid: str | None = None
        self.draws = 0
        self.hidden = 0


class Observation:
    """What a piece of work did in its context, from the moment it was observed: whether it drew luck, and whether it
    read a value hidden from the acting agent. Work that did neither in an unchanged state is pure (:meth:`pure`): run
    again, it gives the same result and costs nothing."""

    __slots__ = ("_context", "_draws", "_hidden")

    def __init__(self, context: Context):
        self._context = context
        self._draws = context.draws
        self._hidden = context.hidden

    @property
    def drew(self) -> bool:
        return self._context.draws != self._draws

    @property
    def read_hidden(self) -> bool:
        return self._context.hidden != self._hidden

    @property
    def spends(self) -> bool:
        """Whether the work drew luck or read a value hidden from the acting agent: an attempt that did is spent (see
        runtime/ledger.py)."""
        return self.drew or self.read_hidden

    def pure(self, before: Any, after: Any) -> bool:
        """Whether the work drew nothing, read nothing hidden, and left the state as it found it (``before`` and
        ``after`` are the state's versions around it)."""
        return before == after and not self.spends


#: The context running in this thread or asyncio task, as ``(randomness, context)``. A context variable rather than a
#: thread-local, so async participants sharing one event-loop thread each keep their own.
_CURRENT: ContextVar[tuple[Randomness, Context] | None] = ContextVar("fg_env_luck", default=None)


class Randomness:
    """The luck of one run: its seed tree, its main stream, how many times each draw site has drawn this round, and the
    context of each thread or task running its logic."""

    def __init__(self, seeds: SeedTree, main: random.Random, firings: dict[str, int] | None = None,
                 births: dict[str, int] | None = None):
        self.seeds = seeds
        #: What logic outside any draw site, turn or view draws from.
        self.main = main
        #: How many times each draw site has drawn this round (see :class:`~fg_env.sampling.seeds.DrawSite`). Part of
        #: the run's state, never undone.
        self.firings: dict[str, int] = {} if firings is None else firings
        #: How many entities each draw site has created this round (see :meth:`birth`). Part of the run's state and,
        #: unlike luck drawn, journaled with the world: an undone creation gives its count back, so an attempt that
        #: left no trace shifts no later creation's luck.
        self.births: dict[str, int] = {} if births is None else births
        self._threads = threading.local()

    def copy(self) -> Randomness:
        """The same luck, drawing on apart from this one's from here on (no context is copied)."""
        main = random.Random.__new__(random.Random)
        main.setstate(self.main.getstate())
        return Randomness(self.seeds, main, dict(self.firings), dict(self.births))

    def new_round(self) -> None:
        """A round begins: its draw sites have drawn and created nothing yet."""
        self.firings.clear()
        self.births.clear()

    # -- the running context ---------------------------------------------------------------------------------------

    def here(self) -> Context:
        """The context of the running thread or task."""
        current = _CURRENT.get()
        if current is not None and current[0] is self:
            return current[1]
        context = getattr(self._threads, "context", None)
        if context is None:
            context = self._threads.context = Context()
        return context

    def current(self, round: int) -> Any:
        """The stream to draw from now (see the module's rule), counting the draw; ``round`` is the round it is drawn
        in. A forbidden draw raises instead."""
        context = self.here()
        forbid = context.forbid
        if forbid is not None:
            if forbid:
                raise ExprError(forbid)
            raise LuckAhead()
        context.draws += 1
        rng = context.rng
        if rng.__class__ is DrawSite:
            return rng.open(self, round)
        return rng or self.main

    def count_hidden_read(self) -> None:
        """Game logic read a value hidden from the acting agent: a refusal after it spends the action."""
        self.here().hidden += 1

    def observe(self) -> Observation:
        """Watch what the running context draws and reads hidden from now on."""
        return Observation(self.here())

    @contextmanager
    def apart(self) -> Iterator[None]:
        """Work inside the block is not the running context's own (the run's diagnostics trying a tool's choices): what
        it draws and reads hidden counts toward no observation outside it. What it draws stays spent."""
        context = self.here()
        draws, hidden = context.draws, context.hidden
        try:
            yield
        finally:
            context.draws, context.hidden = draws, hidden

    @contextmanager
    def turn_context(self, rng: Any, pending: Any, deadline: float | None = None) -> Iterator[None]:
        """Inside the block — in this thread or asyncio task only — draws come from ``rng``, ``$pending`` reads
        ``pending`` (a :class:`~fg_env.runtime.ledger.Pending`, or None) and the turn ends at ``deadline``
        (``time.monotonic()``; None: no limit), which host calls made in it respect. Blocks nest (a reaction inside a
        turn) and restore on exit."""
        token = _CURRENT.set((self, Context(rng, pending, deadline)))
        try:
            yield
        finally:
            _CURRENT.reset(token)

    def turn_stream(self, round: int, number: int) -> LazyStream:
        """The stream turn ``number`` of ``round`` draws from outside a block of logic."""
        return self.seeds.lazy_rng("turn", round, number)

    def switch(self, rng: Any) -> None:
        """Draw from ``rng`` from here on in the running context (a turn reseeded part-way)."""
        self.here().rng = rng

    # -- where a block draws from ----------------------------------------------------------------------------------

    @contextmanager
    def using(self, rng: Any) -> Iterator[None]:
        """Inside the block the running context draws from ``rng``, then from the stream it used before."""
        context = self.here()
        previous = context.rng
        context.rng = rng
        try:
            yield
        finally:
            context.rng = previous

    def stream(self, *path: PathPart) -> Any:
        """:meth:`using` the stream of ``path``, seeded at its first draw: what an agent reads is drawn from a stream
        of its own (looking again shows the same noise), and so is who a stage wakes."""
        return self.using(self.seeds.lazy_rng(*path))

    def at(self, site: str, owner: Any = None) -> Any:
        """:meth:`using` the stream of the draw site ``site`` — where a block of logic is written — as the block of
        ``owner`` (an action's actor, an `each` item) when it is an entity: each entity has luck of its own, which
        others coming or going never shifts."""
        return self.using(DrawSite(f"{site}@{owner.luck or owner.id}" if isinstance(owner, Entity) else site))

    def birth(self, round: int, journal: Callable[[tuple[Any, ...]], None]) -> str | None:
        """The key of the luck of an entity created now in ``round``: the block creating it — where it is written and
        whose block it runs as — and how many that block has created this round, so what other agents create never
        shifts it. The count's change goes to ``journal`` (the world's), so undoing the creation gives it back. None
        outside a block of logic, or in a trial (see :meth:`forbidden`): the entity is keyed by its id."""
        context = self.here()
        rng = context.rng
        if rng.__class__ is not DrawSite or context.forbid is not None:
            return None  # a trial draws nothing, so what it creates needs no luck, and it spends no count
        count = self.births.get(rng.key, 0)
        journal(("birth", rng.key, count))
        self.births[rng.key] = count + 1
        return f"{rng.key}#{round}.{count}"

    def item(self, site: str, item: Any) -> Any:
        """:meth:`using` a stream of its own for one item of the loop written at ``site``, inside the block running now:
        keyed by that block's stream, the loop and the item (an entity by its id), so neither the items the loop goes
        over nor what they draw shift another item's luck or the draws the block makes after the loop."""
        outer = self.here().rng
        base = f"{outer.key}/" if outer.__class__ is DrawSite else ""
        return self.using(DrawSite(f"{base}{site}@{item.luck or item.id}" if isinstance(item, Entity)
                                   else f"{base}{site}"))

    @contextmanager
    def forbidden(self, reason: str | None = None) -> Iterator[None]:
        """Inside the block a draw is not made. Given a ``reason``, it fails as a rule does with that text: where
        nothing may be left to luck (whether a call is allowed). Without one it raises :class:`LuckAhead`: a trial of a
        call, which must not learn its luck (see ``ActionBook.trial``)."""
        context = self.here()
        previous = context.forbid
        context.forbid = reason or ""
        try:
            yield
        finally:
            context.forbid = previous

    @contextmanager
    def acting_as(self, actor: Entity, action: str | None = None) -> Iterator[None]:
        """Inside the block the rules run for ``actor``'s ``action``: a value hidden from it that they read is counted
        (:meth:`count_hidden_read`), and their refusals may not tell it one."""
        context = self.here()
        previous = context.actor, context.action
        context.actor, context.action = actor, action
        try:
            yield
        finally:
            context.actor, context.action = previous


#: The streams each event draws from — for its `when`, for its `do` — named for what it was written as before events
#: absorbed triggers and stage hooks, so every contract keeps its luck: a round event is counted among round events
#: (``n``), a change event among change events, a stage event draws from its hook's stream, and an event on `create` or
#: `remove` (which draws with the change it runs in) is named by its place among all events. Frozen: a name is a seed.
LEGACY_STREAMS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "round": ("events[{n}]", "events[{n}].do"),
    "change": ("triggers[{n}].when", "triggers[{n}].do"),
    "stage.start": ("stages.{stage}.on_enter", "stages.{stage}.on_enter"),
    "stage.end": ("stages.{stage}.on_exit", "stages.{stage}.on_exit"),
    "stage.turn": ("stages.{stage}.on_idle", "stages.{stage}.on_idle"),
    "other": ("events[{n}]", "events[{n}].do"),
})


def event_streams(anchors: Iterable[str]) -> list[tuple[str, str]]:
    """The streams (:data:`LEGACY_STREAMS`) of events on ``anchors`` (each event's `on`, in declaration order)."""
    streams: list[tuple[str, str]] = []
    counted = {"round": 0, "change": 0}
    fill: dict[str, Any]
    for index, anchor in enumerate(anchors):
        kind, _, rest = anchor.partition(".")
        if kind in counted:
            names, fill = LEGACY_STREAMS[kind], {"n": counted[kind]}
            counted[kind] += 1
        elif kind == "stage":
            stage, _, point = rest.rpartition(".")
            names, fill = LEGACY_STREAMS[f"stage.{point}"], {"stage": stage}
        else:
            names, fill = LEGACY_STREAMS["other"], {"n": index}
        streams.append((names[0].format_map(fill), names[1].format_map(fill)))
    return streams
