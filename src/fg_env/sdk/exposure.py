"""The exposure log: what each agent actually received on every wake, and what it did.

A run records it when asked (``fg_env.load(..., exposures=True)``) or when the contract calls
``$seen`` (its rules depend on it). It is off by default: a coded crowd of thousands of agents
would otherwise keep a record per wake that nobody reads.

``result.exposures`` is JSON-safe: ``{"texts": {hash: text}, "wakes": [record, ...], "chance": [pick, ...]}``.
Every text an agent read — brief, update, view blocks, tool definitions, call results — is stored once
under its content hash, so a brief read on a hundred turns costs one copy. A wake record::

    {"wake": 0, "entity": "ana", "type": "seller", "round": 1, "stage": "pricing", "turn": 1,
     "kind": "turn" | "reaction", "reason": "It is your turn.", "time": 3.5, "time_limit": 30,
     "brief": {"hash", "chars", "tokens"} | null,       # null: never read
     "update": {"hash", "chars", "tokens"} | null,
     "views": [{"name", "hash", "look": true?}],         # view blocks shown in the update or by look
     "news": [seq, ...],                                 # log events delivered as "Since your last turn"
     "entries": [entry seq, ...],                        # record entries shown (as news or in a view)
     "view_events": [seq, ...],                          # log events listed inside views
     "tools": [name, ...], "tool_sets": [hash, ...],     # names offered; each distinct definition set
     "calls": [{"tool", "args", "ok", "ended", "result": hash, "error"?, "assets": [hash, ...]?}],
     "assets": [{"id", "hash", "in": "brief" | "update" | "call"}]?,   # files delivered (only when any were)
     "invalid": 0, "timed_out": false, "undone": 0, "usage": {...}?, "late_usage": {...}?,
     "steps": [["brief"], ["update"], ["tools"], ["call", tool, args], ["usage", {...}], ["timeout"], ...]}

``steps`` is the turn's entry on the engine's tape (:mod:`fg_env.sdk.replay`): everything the participant did
through its wake, in order — first reads, calls, reported usage, a timeout — which is what a replay plays back.
``late_usage`` is model usage the participant reported after its turn was over (it ran out of time): counted in
``usage`` and the run's statistics all the same, though that participant could no longer act.

``chance`` lists, in order, every outcome a chooser picked (``fg_env.load(..., chance=callable)``, a game, a
copy): ``{"chance": name, "site": path, "index": i, "label": text, "round": n}``. Sampled outcomes need no entry:
the seed replays them. A run that continues a fork adds ``start``: the snapshot it continued from, its exposure
log given as counts (``{"wakes": n, "chance": m}``, the first entries of this log), which a replay restores.

Only what was rendered counts: a coded participant that never reads its update was shown nothing.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Set

from ..entity import Entity
from .world import Entry, LogEvent

if TYPE_CHECKING:
    from .actions import ToolSpec
    from .chance import ChanceNode
    from .contract import Contract
    from .runtime import Env
    from .session import ToolResult
    from .turn import Turn

__all__ = ["Shown", "Exposure", "ExposureLog", "asks_seen", "recording", "text_hash", "tokens"]

#: Hex digits kept from a text's SHA-256: unique for any realistic run, short enough to read.
HASH_DIGITS = 16


def asks_seen(contract: "Contract") -> bool:
    """Whether the contract's rules call `$seen`, so the run must record what agents were shown."""
    return "$seen(" in json.dumps(contract.model_dump(by_alias=True, exclude_defaults=True), default=str)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:HASH_DIGITS]


def tokens(text: str) -> int:
    """A rough token count (four characters a token), the same estimate previews use."""
    return len(text) // 4


class Shown:
    """What one rendering put in front of an agent: view blocks, news events and listed items."""

    __slots__ = ("views", "news", "events", "entries")

    def __init__(self) -> None:
        self.views: List[tuple] = []
        self.news: List[int] = []
        self.events: List[int] = []
        self.entries: List[int] = []

    def item(self, value: Any) -> None:
        """Note an item a view listed, when it is something `$seen` can ask about."""
        if isinstance(value, Entry):
            self.entries.append(value["seq"])
        elif isinstance(value, LogEvent):
            self.events.append(value.seq)


class Exposure:
    """One wake's record, filled in while the turn runs (always under the run's lock)."""

    def __init__(self, log: "ExposureLog", turn: "Turn", kind: str):
        self.log = log
        self.staged = turn.staged
        world = turn.env.world
        record: Dict[str, Any] = {"entity": turn.actor.id, "type": turn.actor.entity_type, "round": turn.round,
                                  "stage": turn.stage.name, "turn": turn.number, "kind": kind,
                                  "reason": str.__str__(turn.reason)}
        if world.continuous:
            record["time"] = world.time
        if turn.time_limit is not None:
            record["time_limit"] = turn.time_limit
        record.update(brief=None, update=None, views=[], news=[], entries=[], view_events=[], tools=[],
                      tool_sets=[], calls=[])
        self.record = record
        self._deferred: List[Shown] = []
        #: The record as appended to the log, once the turn has closed.
        self.logged: Optional[Dict[str, Any]] = None

    def _text(self, text: str) -> Dict[str, Any]:
        return {"hash": self.log.keep(text), "chars": len(text), "tokens": tokens(text)}

    def read_brief(self, text: str) -> None:
        self.record["brief"] = self._text(text)

    def read_update(self, text: str, shown: Shown) -> None:
        self.record["update"] = self._text(text)
        self._show(shown, look=False)

    def looked(self, shown: Shown) -> None:
        self._show(shown, look=True)

    def _show(self, shown: Shown, look: bool) -> None:
        record = self.record
        for name, text in shown.views:
            view: Dict[str, Any] = {"name": name, "hash": self.log.keep(text)}
            if look:
                view["look"] = True
            record["views"].append(view)
        record["news"].extend(shown.news)
        record["view_events"].extend(shown.events)
        record["entries"].extend(shown.entries)
        if self.staged:  # simultaneous turns index when the stage commits, in turn order
            self._deferred.append(shown)
        else:
            self.log.index(record["entity"], shown)

    def shown(self, assets: Iterable[Any], where: str) -> None:
        """Note the files delivered to the agent (their ids and content hashes, never their bytes)."""
        delivered = self.record.setdefault("assets", [])
        delivered.extend({"id": asset.id, "hash": asset.hash, "in": where} for asset in assets)

    def offered(self, tools: Iterable["ToolSpec"]) -> None:
        listed = list(tools)
        record = self.record
        for tool in listed:
            if tool.name not in record["tools"]:
                record["tools"].append(tool.name)
        digest = self.log.keep(json.dumps([t.to_dict() for t in listed], sort_keys=True, ensure_ascii=False))
        if not record["tool_sets"] or record["tool_sets"][-1] != digest:
            record["tool_sets"].append(digest)

    def called(self, name: Any, args: Any, result: "ToolResult") -> None:
        call: Dict[str, Any] = {"tool": name if isinstance(name, str) else repr(name), "args": _jsonable(args),
                                "ok": result.ok, "ended": result.ended, "result": self.log.keep(result.text)}
        error = result.data.get("error") if result.data else None
        if error:
            call["error"] = error
        if result.attachments:
            call["assets"] = [file.hash for file in result.attachments]
            self.shown(result.attachments, "call")
        self.record["calls"].append(call)

    def used(self, counts: Mapping[str, int], late: bool = False) -> None:
        """Add reported model usage; ``late`` usage came after the turn was over and its record was logged."""
        record = self.logged if self.logged is not None else self.record
        for field in ("usage", "late_usage") if late else ("usage",):
            usage = record.setdefault(field, {})
            for key, value in counts.items():
                if value:
                    usage[key] = usage.get(key, 0) + value

    def close(self, turn: "Turn") -> None:
        """Finish the record and append it to the log (called once, in the engine's turn order)."""
        record = self.record
        record["invalid"] = turn.stats.invalid_calls
        record["timed_out"] = turn.timed_out
        record["undone"] = turn.stats.undone_turns
        taped = turn.env.origin.tape.turns.get(turn.number)
        record["steps"] = [_jsonable(list(step)) for step in taped[1]] if taped else []
        for shown in self._deferred:
            self.log.index(record["entity"], shown)
        self._deferred.clear()
        self.logged = self.log.append(record)


class ExposureLog:
    """Every wake's record plus an index answering `$seen`."""

    def __init__(self) -> None:
        self.texts: Dict[str, str] = {}
        self.wakes: List[Dict[str, Any]] = []
        self.chance: List[Dict[str, Any]] = []
        self._events: Dict[str, Set[int]] = {}
        self._entries: Dict[str, Set[int]] = {}
        self._views: Dict[str, Set[str]] = {}

    def keep(self, text: str) -> str:
        plain = str.__str__(text)
        digest = text_hash(plain)
        self.texts.setdefault(digest, plain)
        return digest

    def open(self, turn: "Turn", kind: str) -> Exposure:
        return Exposure(self, turn, kind)

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        logged = {"wake": len(self.wakes), **record}
        self.wakes.append(logged)
        return logged

    def picked(self, node: "ChanceNode", index: int, round: int) -> None:
        """Note an outcome a chooser picked (sampled outcomes need no note: the seed replays them)."""
        self.chance.append({"chance": node.name, "site": node.site, "index": index,
                            "label": node.outcomes[index].label, "round": round})

    def index(self, entity_id: str, shown: Shown) -> None:
        self._events.setdefault(entity_id, set()).update(shown.news, shown.events)
        self._entries.setdefault(entity_id, set()).update(shown.entries)
        self._views.setdefault(entity_id, set()).update(name for name, _ in shown.views)

    def seen(self, viewer: Any, item: Any) -> Optional[bool]:
        """Whether ``viewer`` was shown ``item`` (an event, a record entry, or a view by name); None when
        ``item`` is none of those."""
        viewer_id = viewer.id if isinstance(viewer, Entity) else viewer
        if isinstance(item, LogEvent):
            return item.seq in self._events.get(viewer_id, ())
        if isinstance(item, Entry):
            return item.get("seq") in self._entries.get(viewer_id, ())
        if isinstance(item, str):
            return item in self._views.get(viewer_id, ())
        return None

    def to_dict(self) -> Dict[str, Any]:
        # Sorted: concurrent turns store their texts in whatever order they finish.
        return {"texts": dict(sorted(self.texts.items())), "wakes": [_detached(w) for w in self.wakes],
                "chance": [dict(pick) for pick in self.chance]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExposureLog":
        log = cls()
        log.texts = dict(data.get("texts") or {})
        log.chance = [dict(pick) for pick in data.get("chance") or []]
        for record in data.get("wakes") or []:
            log.wakes.append(dict(record))
            shown = Shown()
            shown.news, shown.events, shown.entries = record["news"], record["view_events"], record["entries"]
            shown.views = [(view["name"], "") for view in record["views"]]
            log.index(record["entity"], shown)
        return log


def recording(env: "Env") -> Dict[str, Any]:
    """``result.exposures``: the exposure log and, for a run that continues a fork, the ``start`` it replays from."""
    log = env.world.exposures
    if log is None:
        return {}
    data = log.to_dict()
    if env.origin.start is not None:
        data["start"] = env.origin.start
    return data


def _detached(record: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy of a logged wake that usage reported later (added to the log's own record) leaves unchanged."""
    return {**record, **{key: dict(record[key]) for key in ("usage", "late_usage") if key in record}}


def _jsonable(value: Any) -> Any:
    """``value`` as plain JSON data (participant arguments may hold anything)."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError, RecursionError):
        return repr(value)
