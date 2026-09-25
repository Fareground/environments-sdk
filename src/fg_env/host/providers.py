"""Calling a model provider: which failures are worth retrying and how long to wait, how long one request may take,
what a failure says, and how a reply is read — shared by the LLM participants, the hosts' reference adapters and the
author loop, so each treats a provider the same way.

A reply is read in one place, whatever shape a client or proxy gives it (:func:`reply_text`, :func:`openai_calls`,
:func:`anthropic_blocks`): objects or plain dicts, content as text or as a list of parts, a tool call's name missing or
not text (read as ``""``, which the engine refuses as no tool), its arguments as JSON text, an object or nothing (read
as the JSON text they mean), an Anthropic tool input given as JSON text (read as the object it holds)."""
from __future__ import annotations

import inspect
import json
import random
from collections.abc import Mapping
from typing import Any

from ..errors import RunError

__all__ = ["field_of", "block_dict", "reply_text", "openai_calls", "anthropic_blocks", "PROVIDER_CALLS",
           "MAX_BACKOFF_SECONDS", "REQUEST_TIMEOUT", "EmptyReply", "retryable", "too_long", "refuse_awaitable",
           "provider_failure", "backoff", "retry_after", "request_timeout"]


#: Per provider: the call every built-in client of it makes, and its sync client (named in failures).
PROVIDER_CALLS = {"anthropic": ("client.messages.create", "anthropic.Anthropic()"),
                  "openai": ("client.chat.completions.create", "openai.OpenAI()")}


#: HTTP statuses worth retrying: timeouts, conflicts, rate limits, overload and server errors.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


_RETRY_NAMES = ("RateLimit", "Timeout", "Connection", "Overloaded", "InternalServer", "ServiceUnavailable")


MAX_BACKOFF_SECONDS = 60.0


#: The longest one provider request may take (the provider SDKs' own default). A turn with less time left gives each
#: request only what is left, so no request outlives the turn that made it (:func:`request_timeout`).
REQUEST_TIMEOUT = 600.0


class EmptyReply(Exception):
    """The provider answered with no reply in it, or one it says failed (OpenRouter does both now and then): retried
    like an overload."""


def retryable(exc: BaseException) -> bool:
    if isinstance(exc, EmptyReply):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _RETRY_STATUSES
    return any(part in type(exc).__name__ for part in _RETRY_NAMES)


#: What providers say when a request holds more than the model's context (Anthropic, OpenAI and compatible servers).
_TOO_LONG = ("prompt is too long", "context length", "context_length_exceeded", "maximum context", "too many tokens")


def too_long(exc: BaseException) -> bool:
    """Whether a provider refused a request for holding more than the model's context: that turn cannot be played by
    this model, but the next one (with a fresh, shorter conversation) may be."""
    text = str(exc).lower()
    return getattr(exc, "status_code", None) in (400, 413) and any(part in text for part in _TOO_LONG)


def refuse_awaitable(response: Any, call: str, client: str, where: str) -> None:
    """Refuse (:class:`RunError`) what an async client's ``call`` returned — an awaitable, closed so it never lingers
    unawaited — naming the sync ``client`` to pass instead."""
    if not inspect.isawaitable(response):
        return
    if inspect.iscoroutine(response):
        response.close()
    raise RunError(f"{call} returned an awaitable, so this is an async client. Pass the sync client, {client}: "
                   "simultaneous turns already run in parallel, and `await env.arun(...)` keeps your event loop free "
                   "while the run plays", where)


def provider_failure(exc: BaseException, call: str, client: str, model: str, retries: int = 0) -> str:
    """What a failed provider call (``call`` on a ``client``) says: the error, and how to fix it — for an error retrying
    could fix that still failed after ``retries``, to try again later."""
    status = getattr(exc, "status_code", None)
    shown = f"{type(exc).__name__} (HTTP {status})" if isinstance(status, int) else type(exc).__name__
    if retryable(exc):
        again = f" after {retries} retr{'y' if retries == 1 else 'ies'}" if retries else ""
        return (f"{call} still failed{again} with {shown}: {exc}. The provider is down, overloaded or limiting your "
                "rate: try again later, or allow more retries.")
    return f"{call} failed with {shown}: {exc}. {_permanent_fix(exc, client, model)}"


def _permanent_fix(exc: BaseException, client: str, model: str) -> str:
    """How to fix a provider error that retrying cannot."""
    status, name = getattr(exc, "status_code", None), type(exc).__name__
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return f"Check the API key your client was made with, and that the account may use model '{model}'."
    if status == 404 or "NotFound" in name:
        return f"Check that the model id '{model}' is right and available to your account."
    if isinstance(status, int):
        return ("The provider rejected the request; fix what its message names (for instance a field this model "
                "does not accept).")
    return f"The call itself failed: pass the sync client, {client}, or one with its interface; or fix the code."


def backoff(attempt: int, exc: BaseException) -> float:
    """Seconds to wait before retrying after ``exc``: the provider's ``retry-after``, else exponential backoff with
    jitter, so parallel turns that hit a rate limit together do not all retry at the same moment."""
    delay = retry_after(exc)
    if delay is None:
        delay = 2.0 ** attempt * (0.5 + _JITTER.random())
    return min(MAX_BACKOFF_SECONDS, delay)


#: Backoff jitter: its own stream, so retries never touch the global or a run's random state.
_JITTER = random.Random()


def retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = float(headers.get("retry-after")) if headers is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


def request_timeout(left: float | None) -> float:
    """The ``timeout`` of one provider request, given the seconds ``left`` in the turn (None: no limit)."""
    return REQUEST_TIMEOUT if left is None else min(left, REQUEST_TIMEOUT)


def field_of(owner: Any, name: str) -> Any:
    """``owner``'s ``name``: an attribute of a client's response object, or a key of the plain dict some clients and
    proxies return instead; None for none."""
    if owner is None:
        return None
    return owner.get(name) if isinstance(owner, Mapping) else getattr(owner, name, None)


def reply_text(content: Any) -> str:
    """A message's content as text: the text itself, or the text of its parts (a list of text parts, as objects or
    dicts, as some OpenAI-compatible servers send; other parts left out), joined."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = [content] if isinstance(content, Mapping) else content if isinstance(content, (list, tuple)) else []
    return "".join(text for part in parts
                   if isinstance(text := part if isinstance(part, str) else field_of(part, "text"), str))


def _name(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _arguments(value: Any) -> str:
    """A tool call's arguments as the JSON text the model meant: text as written, an object or list as its JSON."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def openai_calls(message: Any) -> list[tuple[Any, str, str]]:
    """The tool calls of an OpenAI-format reply ``message`` as ``(id, name, arguments as JSON text)``."""
    calls = field_of(message, "tool_calls")
    if not isinstance(calls, (list, tuple)):
        return []
    return [(field_of(call, "id"), _name(field_of(field_of(call, "function"), "name")),
             _arguments(field_of(field_of(call, "function"), "arguments"))) for call in calls]


def anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    """An Anthropic reply's ``content`` as plain blocks (:func:`block_dict`), from a list of blocks, one block, or
    text."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks = [content] if isinstance(content, Mapping) else content if isinstance(content, (list, tuple)) else []
    return [block_dict(block) for block in blocks]


def _input(value: Any) -> dict[str, Any]:
    """An Anthropic tool input as the object it is: given as JSON text, the object that text holds."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def block_dict(block: Any) -> dict[str, Any]:
    """An Anthropic content block (an object, or a plain dict) as the plain dict a later request sends back."""
    if isinstance(block, str):
        return {"type": "text", "text": block}
    kind = field_of(block, "type") or ""
    if kind == "text":
        return {"type": "text", "text": reply_text(field_of(block, "text"))}
    if kind == "tool_use":
        return {"type": "tool_use", "id": field_of(block, "id"), "name": _name(field_of(block, "name")),
                "input": _input(field_of(block, "input"))}
    if hasattr(block, "model_dump"):
        return dict(block.model_dump())
    return dict(block) if isinstance(block, Mapping) else {"type": str(kind)}
