"""Candidate record notifications; live visibility is still checked by the world."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .contract import Contract
from .record_index import FALLBACK, entry_key, viewer_key
from .world_parts import Entry, LogEvent

Key = Tuple[Optional[str], Any]


class RecordEvents:
    def __init__(self, events: Iterable[LogEvent], entries: Mapping[int, Entry], contract: Contract):
        self.rules = {name: spec.visible for name, spec in contract.records.items()}
        self.groups: Dict[Optional[str], Dict[Any, List[LogEvent]]] = {}
        for event in events:
            if event.kind == "record":
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
        return record, author

    def remove(self, event: LogEvent, key: Key) -> None:
        record, author = key
        owners = self.groups[record]
        owners[author].remove(event)
        if not owners[author]:
            del owners[author]
        if not owners:
            del self.groups[record]

    def candidates(self, contract: Contract, viewer: Any) -> List[LogEvent]:
        out: List[LogEvent] = []
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
