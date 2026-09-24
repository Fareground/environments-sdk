"""The log sorted for news, once for every reader, so an agent's update costs the same however busy the world is.

Most events are public — every agent may learn of them, as the same text — so which of them an agent's news shows
depends on the agent only through its own actions, which are never its news. Those events are kept in two lists in
log order (world news, and other agents' actions), so a reader counts and picks the newest of them without reading
the rest. The events whose visibility depends on the reader — sent to a few, or a record entry — are kept in a third
list, which each reader goes through itself.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from itertools import islice

from ..world.live import LogEvent

__all__ = ["NewsIndex"]


class _Kind:
    """One kind of indexed events, in log order."""

    __slots__ = ("events", "seqs")

    def __init__(self) -> None:
        self.events: list[LogEvent] = []
        self.seqs: list[int] = []

    def add(self, event: LogEvent) -> None:
        self.events.append(event)
        self.seqs.append(event.seq)

    def start(self, since: int) -> int:
        """The position of the first event after log position ``since``."""
        return bisect_right(self.seqs, since)


class NewsIndex:
    """The public news (world news, actions) and the reader-dependent events of one log, in log order."""

    __slots__ = ("log", "world", "actions", "own", "private", "_count", "_last", "_last_seq", "_first")

    def __init__(self, log: list[LogEvent]):
        self.log = log
        self.world, self.actions, self.private = _Kind(), _Kind(), _Kind()
        #: actor → the log positions (seq) of its public actions
        self.own: dict[str, list[int]] = {}
        self._count = 0
        self._last: LogEvent | None = None
        self._last_seq = 0
        self._first: LogEvent | None = None

    def current(self, log: list[LogEvent]) -> bool:
        """Whether this index still describes ``log`` up to where it has read (it only ever grows at the end; an
        undone, reordered or forgotten event means reading it again)."""
        count = self._count
        return (log is self.log and count <= len(log)
                and (count == 0 or (log[count - 1] is self._last and self._last.seq == self._last_seq
                                    and log[0] is self._first)))

    def extend(self) -> None:
        """Index the events added to the log since the last read."""
        log = self.log
        for event in log[self._count:]:
            if event.to is not None or event.kind == "record":
                self.private.add(event)
            elif not event.text:
                continue  # public, but says nothing: never news
            elif event.kind == "action":
                self.actions.add(event)
                if event.actor is not None:
                    self.own.setdefault(event.actor, []).append(event.seq)
            else:
                self.world.add(event)
        self._count = len(log)
        if log:
            self._first, self._last, self._last_seq = log[0], log[-1], log[-1].seq

    def reader_dependent(self, since: int) -> list[LogEvent]:
        """The events after ``since`` whose news depends on the reader, in log order."""
        return self.private.events[self.private.start(since):]

    def world_news(self, since: int, newest: int) -> tuple[list[LogEvent], int]:
        """The ``newest`` public world news after ``since`` (newest first), and how many there are."""
        kind = self.world
        start = kind.start(since)
        return kind.events[max(start, len(kind.events) - newest):][::-1], len(kind.events) - start

    def others_actions(self, since: int, actor: str, newest: int) -> tuple[list[LogEvent], int]:
        """The ``newest`` public actions after ``since`` by others than ``actor`` (newest first), and how many there
        are."""
        kind = self.actions
        start = kind.start(since)
        own = self.own.get(actor, ())
        count = len(kind.events) - start - (len(own) - bisect_right(own, since) if own else 0)
        return list(islice(_others(kind.events, start, actor), newest)), count


def _others(events: list[LogEvent], start: int, actor: str) -> Iterator[LogEvent]:
    """The events from position ``start`` on by others than ``actor``, newest first."""
    for index in range(len(events) - 1, start - 1, -1):
        event = events[index]
        if event.actor != actor:
            yield event
