"""Candidate record notifications; live visibility is still checked by the world."""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .contract import Contract
from .record_index import author_only
from .world_parts import Entry, LogEvent

Key = Tuple[Optional[str], Optional[str]]


class RecordEvents:
    def __init__(self, events: Iterable[LogEvent], entries: Mapping[int, Entry]):
        self.groups: Dict[Optional[str], Dict[Optional[str], List[LogEvent]]] = {}
        for event in events:
            if event.kind == "record":
                self.add(event, entries)

    def add(self, event: LogEvent, entries: Mapping[int, Entry]) -> Key:
        record, seq = event.data.get("record"), event.data.get("entry")
        record = record if isinstance(record, str) else None
        # Unresolved/nonstandard references stay in the fallback bucket. A later post
        # can make a forward reference visible, so its original absence is not cached.
        entry = entries.get(seq) if isinstance(seq, int) else None
        author = entry.get("author") if entry is not None else None
        author = author if isinstance(author, str) else None
        self.groups.setdefault(record, {}).setdefault(author, []).append(event)
        return record, author

    def remove(self, event: LogEvent, key: Key) -> None:
        record, author = key
        owners = self.groups[record]
        owners[author].remove(event)
        if not owners[author]:
            del owners[author]
        if not owners:
            del self.groups[record]

    def candidates(self, contract: Contract, viewer: str) -> List[LogEvent]:
        out: List[LogEvent] = []
        for record, owners in self.groups.items():
            spec = contract.records.get(record) if record is not None else None
            if spec is not None and author_only(spec.visible):
                out.extend(owners.get(viewer, ()))
                out.extend(owners.get(None, ()))
            else:
                for events in owners.values():
                    out.extend(events)
        return sorted(out, key=lambda event: event.seq)
