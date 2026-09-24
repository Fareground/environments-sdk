"""What a host implements: the small protocols the engine calls for judgment it cannot compute.

A host is any object with the one method its role needs. The engine never imports a provider
SDK; the reference adapters in :mod:`fg_env.host.adapters` wrap your own client object, and
:mod:`fg_env.host.stubs` holds deterministic stand-ins for tests.

Every request is plain JSON data. Text that participants wrote arrives as plain strings inside
it: a host must treat all of it as information about the environment, never as instructions.
Every answer must be plain JSON data too; the engine validates it, records it for replay and
marks any text in it as untrusted before an agent reads it. An answer it cannot use (outside the
protocol, or the host raised :class:`HostError`) is asked for once more, the request then carrying
``"correction"``: what was wrong with the last answer. A second unusable answer stops the run.

A request may carry files: ``"attachments": [{"id", "type", "media_type", "name", "size", "hash", "caption"?,
"alt"?, "untrusted"?, "data": base64 | "text": text}]`` (a judged exhibit, a described photo). Treat their content
as information too.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

__all__ = ["HostError", "Evaluator", "GameMaster", "Tools", "Writer", "Ranker", "Feed", "Describer"]


class HostError(Exception):
    """A host failed, or answered outside its protocol. Raise it from an adapter to fail cleanly (the engine asks
    once more, with a ``correction``, before it stops the run)."""


@runtime_checkable
class Evaluator(Protocol):
    """Scores text against a rubric (``host.judge`` mechanism).

    Request: ``{"judge", "model", "instructions", "criteria": [{"name", "description", "weight",
    "min", "max"}], "subject", "text", "context": [{"speaker", "text"}], "blind", "out_of"}``.
    Answer: ``{"scores": {criterion: number within [min, max]}, "rationale": text}``.
    """

    def judge(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class GameMaster(Protocol):
    """Turns a free-text attempt into effects chosen from an allow-list (``host.game_master``).

    Request: ``{"game_master", "model", "rules", "actor": {"id", "name", "type", "props"},
    "attempt", "context", "allowed": [rule, ...], "max_effects", "time"}``.
    Answer: ``{"narration": text, "effects": [{"effect": "set", "target", "prop", "value"} |
    {"effect": "set_world", "prop", "value"} | {"effect": "transfer", "prop", "from", "to",
    "amount"} | {"effect": "move", "target", "to"} | {"effect": "news", "text"}]}`` or
    ``{"refuse": reason}``. Anything outside the allow-list makes the engine refuse the attempt.
    """

    def resolve(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class Tools(Protocol):
    """Services agents call inside a turn, such as web search (``host.tool``).

    ``name`` is the host name the contract uses; ``args`` are the validated tool arguments.
    Answer: the result as text.
    """

    def call(self, name: str, args: Mapping[str, Any]) -> str: ...


@runtime_checkable
class Writer(Protocol):
    """Writes text: personas at build time, recaps of long records, memory reflections.

    Request: ``{"task": "persona" | "recap" | "reflection", "model", "prompt", ...task data}``.
    Answer: the text.
    """

    def write(self, request: Mapping[str, Any]) -> str: ...


@runtime_checkable
class Ranker(Protocol):
    """Scores how relevant each memory is to a query (``mind.memory`` with ``relevance: host``).

    Request: ``{"query", "items": [{"id", "text"}]}``. Answer: one number in [0, 1] per item.
    """

    def rank(self, request: Mapping[str, Any]) -> Sequence[float]: ...


@runtime_checkable
class Feed(Protocol):
    """Supplies external data for a contract's ``feeds``: live or historical prices, news, weather.

    Request: ``{"feed", "query", "into", "expects", "round", "time", "date"}`` — ``expects`` is the
    target's shape (``{"type", "values"?, "min"?, "max"?}`` for a world prop, ``{"entries": {field: type}}``
    for a record). Answer: the prop's new value, or one record entry's fields or a list of entries.
    Text in the answer reaches agents marked untrusted.
    """

    def fetch(self, request: Mapping[str, Any]) -> Any: ...


@runtime_checkable
class Describer(Protocol):
    """Describes a file for an asset's ``describe`` host: a caption and the text it holds (a PDF's text, a photo's
    visible writing, a recording's transcript).

    Request: ``{"task": "describe", "asset": {"id", "name", "type", "media_type", "caption", "alt", "tags"},
    "attachments": [the file]}``. Answer: ``{"caption": text, "text": text}``.
    """

    def describe(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
