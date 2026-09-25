"""Candidate record notifications; live visibility is still checked by the world.

The index holds the notifications of retained entries only: an entry a record's `keep` dropped is seen by nobody, so
its notifications are dropped with it (:meth:`RecordEvents.drop`) and the index stays the size of what is kept. An undo
removes the newest notification, so it is popped from the end of its group.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..contract import Contract
from .parts import Entry, LogEvent
from .record_index import FALLBACK, entry_key, viewer_key

Key = tuple[str | None, Any]


class RecordEvents:
    def __init__(self, events: Iterable[LogEvent], entries: Mapping[int, Entry], contract: Contract,
                 last_entry: int = 0):
        """Index the notifications among ``events``, but for those of entries up to ``last_entry`` (the record
        sequence number posted last) that are no longer kept."""
        self.rules = {name: spec.visible for name, spec in contract.records.items()}
        self.groups: dict[str | None, dict[Any, list[LogEvent]]] = {}
        #: Each entry's notifications (by its sequence number) with their groups, so dropping the entry drops them.
        self.by_entry: dict[int, list[tuple[Key, LogEvent]]] = {}
        for event in events:
            seq = event.data.get("entry") if event.kind == "record" else None
            if event.kind == "record" and not (isinstance(seq, int) and seq <= last_entry and seq not in entries):
                self.add(event, entries)

    def add(self, event: LogEvent, entries: Mapping[int, Entry]) -> Key:
        record, seq = event.data.get("record"), event.data.get("entry")
        record = record if isinstance(record, str) else None
        # Unresolved/nonstandard references stay in the fallback bucket. A later post
        # can make a forward reference visible, so its original absence is not cached.
        entry = entries.get(seq) if isinstance(seq, int) else None
        rule = self.rules.get(record, "all") if record is not None else "all"
        author = entry_key(rule, entry) if entry is not None else FALLBACK
        self.groups.setdefault(record, {}).setdefault(author, []).append(event)
        if isinstance(seq, int):
            self.by_entry.setdefault(seq, []).append(((record, author), event))
        return record, author

    def remove(self, event: LogEvent, key: Key) -> None:
        """Take back the notification ``event`` (an undo: the newest of its group)."""
        seq = event.data.get("entry")
        if isinstance(seq, int) and seq in self.by_entry:
            indexed = self.by_entry[seq]
            indexed.pop(next(i for i in range(len(indexed) - 1, -1, -1) if indexed[i][1] is event))
            if not indexed:
                del self.by_entry[seq]
        self._discard(event, key)

    def drop(self, seqs: Iterable[int]) -> list[int]:
        """The entries ``seqs`` are no longer kept: drop their notifications. Their events' sequence numbers."""
        dropped = []
        for seq in seqs:
            for key, event in self.by_entry.pop(seq, ()):
                self._discard(event, key)
                dropped.append(event.seq)
        return dropped

    def _discard(self, event: LogEvent, key: Key) -> None:
        record, author = key
        owners = self.groups[record]
        events = owners[author]
        if events[-1] is event:  # an undo takes back the newest
            events.pop()
        else:  # a dropped entry's notification is among the oldest
            events.remove(event)
        if not events:
            del owners[author]
        if not owners:
            del self.groups[record]

    def candidates(self, contract: Contract, viewer: Any) -> list[LogEvent]:
        out: list[LogEvent] = []
        for record, owners in self.groups.items():
            spec = contract.records.get(record) if record is not None else None
            key = viewer_key(spec.visible, viewer) if spec is not None else FALLBACK
            if key is not FALLBACK:
                out.extend(owners.get(key, ()))
                out.extend(owners.get(FALLBACK, ()))
            else:
                for events in owners.values():
                    out.extend(events)
        return sorted(out, key=lambda event: event.seq)
