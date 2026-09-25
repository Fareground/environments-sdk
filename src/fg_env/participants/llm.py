"""LLM participants: :func:`anthropic` and :func:`openai` drive an agent's turn with a model's tool calls on your own
client.

The loop sends the turn's brief, update and tools, runs each tool call the model makes through the turn, and sends the
results back until the turn ends; it retries rate limits and overload with backoff, keeps the prompt prefix cacheable,
and counts tokens against the run's budget. ``<provider>:<model>`` participants run on the official client."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import os
import random
import threading
import time
from collections.abc import Callable, Collection, Mapping
from typing import TYPE_CHECKING, Any

from ..actions.params import parse_arguments
from ..assets.multimodal import ANTHROPIC_MEDIA, OPENAI_MEDIA, anthropic_parts, media_set, openai_parts
from ..errors import RunError
from ..host.usage import call_usage
from ..information.schemas import ToolSpec
from ..runtime.budget import tokens_of
from ..runtime.facts import Stats
from ..runtime.session import END_TURN, ToolResult, Wake

if TYPE_CHECKING:
    from .builtin import Participant

__all__ = ["PROVIDERS", "anthropic", "openai", "official_client", "official_participant", "provider_failure"]


#: ``<provider>:<model>`` participants: the official client's class, and the variable holding its API key.
PROVIDERS = {"anthropic": ("Anthropic", "ANTHROPIC_API_KEY"), "openai": ("OpenAI", "OPENAI_API_KEY")}


def official_participant(provider: str, model: str) -> Participant:
    """The LLM participant ``<provider>:<model>`` names, on the provider's client made from the environment's key."""
    make = anthropic if provider == "anthropic" else openai
    return make(official_client(provider, model), model)


def official_client(provider: str, model: str) -> Any:
    """The official ``anthropic`` or ``openai`` client for ``<provider>:<model>``, made from the environment's key."""
    client_class, key = PROVIDERS[provider]
    if not model:
        raise ValueError(f"'{provider}:' names no model: use '{provider}:<model>'")
    if not os.environ.get(key):
        raise ValueError(f"'{provider}:{model}' needs an API key: set {key} in the environment")
    try:
        module = importlib.import_module(provider)
    except ImportError:
        raise ValueError(f"'{provider}:{model}' needs the {provider} package: pip install {provider}") from None
    return getattr(module, client_class)()


class _LLMUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.retries = 0
        self.forfeits = 0
        self.truncated = 0
        self.refusals = 0
        self.out_of_steps = 0
        self.no_tool_replies = 0
        self.unreported_usage = 0
        self.too_long = 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


#: HTTP statuses worth retrying: timeouts, conflicts, rate limits, overload and server errors.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


_RETRY_NAMES = ("RateLimit", "Timeout", "Connection", "Overloaded", "InternalServer", "ServiceUnavailable")


_MAX_BACKOFF_SECONDS = 60.0

#: The longest one provider request may take (the provider SDKs' own default). A turn with less time left gives each
#: request only what is left, so no request outlives the turn that made it (:func:`request_timeout`).
REQUEST_TIMEOUT = 600.0


#: What a reply cut off at the output limit is asked, once, when ``retry_truncated`` is on.
_TRUNCATED = ("Your reply was cut off at the output limit before it called a tool. Answer now with a tool call; "
              "keep your reasoning short.")


#: What a leftover call in a reply gets once the turn has ended (it is not sent to the engine).
_NOT_RUN = "Not done: your turn was already over."


#: The shortest prompt prefix worth a cache breakpoint, in tokens: no Anthropic model caches a shorter one (the
#: minimum is 512 tokens on the newest models, up to 4096 on others). A breakpoint below a model's own minimum costs
#: nothing — the prefix is simply not cached — so the bar is the lowest minimum, never a higher one that would give up
#: reads on the models that cache from 512.
_CACHE_MIN_TOKENS = 512


def _tokens(*parts: Any) -> int:
    """A rough token count of request parts' text (about four characters a token; files' encoded bytes are left out):
    enough to tell whether a prefix can be cached, and what a call about to be made will cost."""
    return _chars(parts) // 4


def _chars(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(len(str(key)) + _chars(item) for key, item in value.items()
                   if key not in ("data", "file_data", "url"))
    if isinstance(value, (list, tuple)):
        return sum(_chars(item) for item in value)
    return len(str(value))


class _TurnTools:
    """The tools a model is shown for a whole turn: the ones legal when the turn starts, in the engine's order, so every
    call of the turn sends the same prompt prefix and reads the one before it from the prompt cache. The engine still
    checks every call and refuses one that is no longer legal with the reason. A tool that becomes legal during the turn
    is added; the result of each call names the offered tools that are not legal any more."""

    def __init__(self, wake: Wake, provider: str):
        self._convert = ToolSpec.to_anthropic if provider == "anthropic" else ToolSpec.to_openai
        self.names: list[str] = []
        self.definitions: list[dict[str, Any]] = []
        self._add(wake.tools)

    def _add(self, tools: list[ToolSpec]) -> None:
        for tool in tools:
            if tool.name not in self.names:
                self.names.append(tool.name)
                self.definitions.append(self._convert(tool))

    def changes(self, wake: Wake) -> str:
        """What changed in the legal tools since the turn started, to add to a tool result ("" when nothing did)."""
        if wake.done:
            return ""
        tools = wake.tools
        self._add(tools)
        legal = {tool.name for tool in tools}
        gone = [name for name in self.names if name not in legal]
        return f" (Not available now: {', '.join(gone)}.)" if gone else ""


def _nudge(wake: Wake) -> str:
    """A reminder to act through tools, naming the tools offered now (end_turn only when ending is allowed)."""
    tools = wake.tools
    listed = ", ".join(tool.name for tool in tools)
    if any(tool.name == END_TURN for tool in tools):
        return f"Act only by calling your tools ({listed}). When you have nothing more to do, call end_turn."
    return f"Act only by calling your tools ({listed}). You must take an action this turn."


def _may_end(wake: Wake) -> bool:
    """Whether ending the turn is allowed now (a must-act stage refuses it while an action is available)."""
    return any(tool.name == END_TURN for tool in wake.tools)


def _add_user_text(messages: list[dict[str, Any]], text: str) -> None:
    """Add ``text`` to the user message the conversation ends with (a reply with no content keeps no assistant turn)."""
    last = messages[-1]
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    last["content"] = blocks + [{"type": "text", "text": text}]


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, _EmptyReply):
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


def provider_failure(exc: BaseException, call: str, client: str, model: str, retries: int = 0) -> str:
    """What a failed provider call (``call`` on a ``client``) says: the error, and how to fix it — for an error retrying
    could fix that still failed after ``retries``, to try again later."""
    status = getattr(exc, "status_code", None)
    shown = f"{type(exc).__name__} (HTTP {status})" if isinstance(status, int) else type(exc).__name__
    if _retryable(exc):
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
        return ("The provider rejected the request; fix what its message names (for instance a field passed in "
                "`extra` that this model does not accept).")
    return f"The call itself failed: pass the sync client, {client}, or one with its interface; or fix the code."


def _backoff(attempt: int, exc: BaseException) -> float:
    """Seconds to wait before retrying after ``exc``: the provider's ``retry-after``, else exponential backoff with
    jitter, so parallel turns that hit a rate limit together do not all retry at the same moment."""
    delay = _retry_after(exc)
    if delay is None:
        delay = 2.0 ** attempt * (0.5 + _JITTER.random())
    return min(_MAX_BACKOFF_SECONDS, delay)


#: Backoff jitter: its own stream, so retries never touch the global or a run's random state.
_JITTER = random.Random()


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = float(headers.get("retry-after")) if headers is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


class _Forfeit(Exception):
    """A provider call still failed after its retries, or its prompt is longer than the model takes (``too_long``):
    the turn is lost, not the run."""

    def __init__(self, too_long: bool = False):
        super().__init__()
        self.too_long = too_long


class _Over(Exception):
    """The turn ended (its time, or the run's token budget, ran out) before the next model call: none is made."""


class _EmptyReply(Exception):
    """The provider answered with no reply in it, or one it says failed (OpenRouter does both now and then): retried
    like an overload."""


def _over(wake: Wake) -> bool:
    """Whether the turn is over, or its time is up and the engine is about to close it."""
    left = wake.time_left
    return wake.done or (left is not None and left <= 0)


def request_timeout(left: float | None) -> float:
    """The ``timeout`` of one provider request, given the seconds ``left`` in the turn (None: no limit)."""
    return REQUEST_TIMEOUT if left is None else min(left, REQUEST_TIMEOUT)


def _extra(extra: Mapping[str, Any] | None, sent: Collection[str]) -> dict[str, Any]:
    """The ``extra`` request fields, refusing any of the fields the participant sends itself (``sent``)."""
    if extra is None:
        return {}
    if not isinstance(extra, Mapping):
        raise ValueError(f"extra must be a mapping of request fields, such as {{'temperature': 0}}; got {extra!r}")
    clash = [key for key in extra if key in sent]
    if clash:
        raise ValueError(f"extra cannot set {', '.join(map(repr, clash))}: the participant sends it itself (the model, "
                         "system prompt, max_tokens and reasoning_effort are its own arguments, and each request's "
                         "timeout follows the turn's time_limit)")
    return dict(extra)


class _LLMParticipant:
    """The shared tool loop: retries, usage accounting, failing loudly on errors retrying cannot fix."""

    #: Sealed turns run at the same time: each waits on the provider.
    concurrent = True
    #: The provider's sync client, and the call the loop makes on it (named in error messages).
    CLIENT = ""
    CALL = ""
    #: The provider whose usage fields its replies carry (see host/usage.py).
    PROVIDER = ""

    def __init__(self, client: Any, model: str, max_steps: int, system: str, retries: int,
                 media: frozenset = frozenset(), retry_truncated: bool = True, extra: dict[str, Any] | None = None):
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError(f"retries must be a whole number ≥ 0, got {retries!r}")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError(f"max_steps must be a whole number ≥ 1, got {max_steps!r}")
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.system = system
        self.retries = retries
        #: More request fields sent with every call (``extra``).
        self.extra = extra or {}
        #: Attachment types sent as real content; the rest reach the model as their text references only.
        self.media = media
        #: A reply cut off at the output limit without a tool call is asked once more for a short tool call.
        self.retry_truncated = retry_truncated
        self.usage = _LLMUsage()
        self._usage_lock = threading.Lock()
        #: The budget tokens this participant's latest model call spent: what the next call reserves of a token budget
        #: (0 before its first, which reserves its prompt and the most the reply may write: :meth:`_expected`).
        self._last_cost = 0
        #: The most one reply may write (``max_tokens``), or None when the request sets no limit.
        self.output_cap: int | None = None

    def __call__(self, wake: Wake) -> None:
        if any(tool.kind == "act" for tool in wake.tools):  # with nothing to do, the model is not asked
            try:
                self._turn(wake)
            except _Forfeit as lost:
                self._record(wake, forfeits=1, too_long=int(lost.too_long))
            except _Over:
                pass
        if not wake.done and _may_end(wake):
            wake.end()  # in a must-act stage the engine closes the turn instead, and reports that the agent did not act

    def _turn(self, wake: Wake) -> None:
        raise NotImplementedError

    def _follow_up(self, wake: Wake, truncated: bool, asked: bool) -> str | None:
        """What to tell a model whose reply called no tool, or None to end the loop: one follow-up per turn — the
        short retry after a truncated reply (when ``retry_truncated``), else the nudge naming the tools offered. A
        reply that still calls no tool after the nudge, in a turn that took no action, is counted
        (``no_tool_replies``): the turn failed. (A truncated reply is counted as one already.)"""
        if wake.done:
            return None
        if asked or (truncated and not self.retry_truncated):
            turn = wake._turn
            if asked and not truncated and not turn.stats.actions and not turn.ledger.intents:
                self._record(wake, no_tool_replies=1)
            return None
        return _TRUNCATED if truncated else _nudge(wake)

    def _out_of_steps(self, wake: Wake) -> None:
        """The loop made all its ``max_steps`` model calls and the turn is still open: count it (the turn then ends)."""
        if not wake.done:
            self._record(wake, out_of_steps=1)

    @staticmethod
    def _dispatch(wake: Wake, name: Any, args: Any) -> ToolResult | None:
        """Run one tool call of a reply, or None when an earlier call of the same reply ended the turn: leftover
        calls are answered without reaching the engine, so they are never counted."""
        if wake.done:
            return None
        return wake.call(name, args)

    def _create(self, wake: Wake, request: Callable[[], Any], prompt: int) -> Any:
        """One provider call (``prompt``: its rough input tokens), its usage counted. Rate limits, timeouts, overload,
        server errors and empty replies are retried while the turn lasts, never sleeping past its deadline; when the
        retries run out the turn is forfeited, and so is one whose prompt is too long for the model (the next turn
        starts a fresh conversation). Any other error fails the run: retrying would send the same request again. Once
        the turn is over no call is made (:class:`_Over`)."""
        for attempt in range(self.retries + 1):
            if _over(wake):
                raise _Over()
            try:
                return self._call(wake, request, prompt)
            except (RunError, _Over):
                raise
            except Exception as exc:
                if too_long(exc):
                    raise _Forfeit(too_long=True) from exc
                if not _retryable(exc):
                    raise self._failure(wake, exc) from exc
                if attempt >= self.retries:
                    raise _Forfeit() from exc
                self._record(wake, llm_retries=1)
                left = wake.time_left
                time.sleep(_backoff(attempt, exc) if left is None else min(_backoff(attempt, exc), left))
        raise AssertionError("unreachable")

    def _call(self, wake: Wake, request: Callable[[], Any], prompt: int) -> Any:
        """Make one model call and count its usage. Under a token budget the call first reserves what it is expected to
        spend (:meth:`_expected`), waiting while the calls under way may spend what is left, so parallel turns do not
        all overshoot it."""
        turn = wake._turn
        budget = turn.env.budget
        held = budget.reserve(turn.env, turn, lambda: self._expected(prompt)) if budget is not None else 0
        if held is None:
            raise _Over()
        try:
            response = request()
            if inspect.isawaitable(response):
                if inspect.iscoroutine(response):
                    response.close()  # never awaited: closed so it does not linger
                raise RunError(f"{self.CALL} returned an awaitable, so this is an async client. Pass the sync client, "
                               f"{self.CLIENT}: simultaneous turns already run in parallel, and `await env.arun(...)` "
                               "keeps your event loop free while the run plays", f"participant:{wake.entity_id}")
            spent = call_usage(getattr(response, "usage", None), self.PROVIDER, prompt)
            self._record(wake, llm_calls=1, **spent.counts(), unreported_usage=int(spent.unreported))
        finally:
            if budget is not None:
                budget.release(turn.env, held)
        if self._empty(response):
            raise _EmptyReply(getattr(response, "error", None) or "the provider sent no reply")
        return response

    def _expected(self, prompt: int) -> float:
        """What a model call with a ``prompt`` of this many tokens is expected to spend: what this participant's
        previous call spent; before any, the prompt and the most the reply may write (unknown, ``math.inf``, when no
        ``max_tokens`` is set, so the first call runs alone). So the first wave of parallel turns cannot all go through
        on their prompts alone and overshoot a budget by a call each."""
        if self._last_cost:
            return self._last_cost
        return prompt + self.output_cap if self.output_cap is not None else math.inf

    @staticmethod
    def _empty(response: Any) -> bool:
        """Whether a response holds no usable reply: none at all, or one the provider says failed (retried like an
        overload)."""
        return False

    def _failure(self, wake: Wake, exc: BaseException) -> RunError:
        return RunError(provider_failure(exc, self.CALL, self.CLIENT, self.model), f"participant:{wake.entity_id}")

    def _record(self, wake: Wake, **counts: int) -> None:
        if counts.get("llm_calls"):
            self._last_cost = tokens_of(Stats(**{name: counts.get(name, 0) for name in _TOKEN_COUNTS}))
        wake.record_usage(**counts)
        mapping = {"llm_calls": "calls", "llm_retries": "retries"}
        with self._usage_lock:
            for name, value in counts.items():
                attr_name = mapping.get(name, name)
                setattr(self.usage, attr_name, getattr(self.usage, attr_name) + value)


#: The usage counts a model call's budget tokens are made of.
_TOKEN_COUNTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class _Anthropic(_LLMParticipant):
    CLIENT = "anthropic.Anthropic()"
    CALL = "client.messages.create"
    PROVIDER = "anthropic"

    def __init__(self, client: Any, model: str, max_tokens: int, max_steps: int, system: str, retries: int,
                 media: frozenset, retry_truncated: bool, extra: Mapping[str, Any] | None):
        sent = ("model", "messages", "tools", "system", "max_tokens", "timeout")
        super().__init__(client, model, max_steps, system, retries, media, retry_truncated, _extra(extra, sent))
        self.max_tokens = self.output_cap = max_tokens
        #: A digest of the tools and system prompt each agent's latest turn opened with: a turn opening with the same
        #: prefix reads it from the prompt cache, so it is worth a breakpoint even when the turn makes one call.
        self._openings: dict[str, str] = {}

    def _turn(self, wake: Wake) -> None:
        text = (self.system + "\n\n" if self.system else "") + wake.brief
        parts = anthropic_parts(wake.attachments, self.media) if self.media else []
        opening: Any = [{"type": "text", "text": wake.update}, *parts] if parts else wake.update
        messages: list[dict[str, Any]] = [{"role": "user", "content": opening}]
        offered = _TurnTools(wake, "anthropic")
        several = _several_calls(wake.tools)
        prefix = hashlib.sha256(json.dumps([offered.definitions, text], default=str).encode()).hexdigest()
        repeated = self._openings.get(wake.entity_id) == prefix
        self._openings[wake.entity_id] = prefix
        asked = False
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = offered.definitions
            head = _tokens(tools, text)
            system: list[dict[str, Any]] = [{"type": "text", "text": text}]
            if head >= _CACHE_MIN_TOKENS and (several or repeated):
                system[0]["cache_control"] = {"type": "ephemeral"}
            prompt = head + _tokens(messages)
            sent = _cached(messages) if several and prompt >= _CACHE_MIN_TOKENS else messages
            response = self._create(wake, lambda: self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system, tools=tools, messages=sent,  # noqa: B023 — called within this iteration
                timeout=request_timeout(wake.time_left), **self.extra), prompt)
            if getattr(response, "stop_reason", None) == "refusal":
                self._record(wake, refusals=1)
                return  # asking again after a refusal only invites another
            truncated = getattr(response, "stop_reason", None) == "max_tokens"
            if truncated:
                self._record(wake, truncated=1)
            content = _reply_blocks(getattr(response, "content", None) or [], truncated)
            calls = [block for block in content if block.get("type") == "tool_use"]
            if not calls:
                follow = self._follow_up(wake, truncated, asked)
                if follow is None:
                    return
                asked = True
                if content:
                    messages += [{"role": "assistant", "content": content}, {"role": "user", "content": follow}]
                else:
                    _add_user_text(messages, follow)
                continue
            messages.append({"role": "assistant", "content": content})
            results = []
            for block in calls:
                result = self._dispatch(wake, block.get("name"), block.get("input"))
                if result is None:
                    results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": _NOT_RUN,
                                    "is_error": True})
                    continue
                files = anthropic_parts(result.attachments, self.media) if self.media and result.attachments else []
                reply: Any = [{"type": "text", "text": result.text}, *files] if files else result.text
                results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": reply,
                                "is_error": not result.ok})
            _add_changes(results[-1], offered.changes(wake))
            messages.append({"role": "user", "content": results})
        self._out_of_steps(wake)


def _several_calls(tools: list[ToolSpec]) -> bool:
    """Whether a turn offering ``tools`` may make more than one model call: some tool other than end_turn leaves the
    turn open. A turn whose every action ends it makes one call, so a cache breakpoint on its conversation would be
    written and never read."""
    return any(tool.kind != "end" and not tool.terminal for tool in tools)


def _add_changes(result: dict[str, Any], changes: str) -> None:
    """Add what changed in the legal tools to the text of a tool result."""
    if not changes:
        return
    content = result["content"]
    if isinstance(content, str):
        result["content"] = content + changes
    else:
        result["content"] = [{**content[0], "text": content[0]["text"] + changes}, *content[1:]]


def _cached(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``messages`` with a cache breakpoint on the latest one, so the turn's next call reads all of it from the prompt
    cache (``messages`` itself is left as it is: only one breakpoint follows the conversation)."""
    last = messages[-1]
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return [*messages[:-1], {**last, "content": blocks}]


def _reply_blocks(blocks: Any, truncated: bool) -> list[dict[str, Any]]:
    """A reply's content blocks as they are sent back: without empty text (the API refuses it), and — for a reply cut
    off at ``max_tokens`` — without its tool calls, whose arguments may be cut off too (none of them is made)."""
    content = [_block_dict(block) for block in blocks]
    return [block for block in content
            if not (block.get("type") == "text" and not block.get("text", "").strip())
            and not (truncated and block.get("type") == "tool_use")]


def _block_dict(block: Any) -> dict[str, Any]:
    kind = getattr(block, "type", None) or (block.get("type") if isinstance(block, Mapping) else "")
    if kind == "text":
        return {"type": "text", "text": _field(block, "text") or ""}
    if kind == "tool_use":
        return {"type": "tool_use", "id": _field(block, "id"), "name": _field(block, "name"),
                "input": _field(block, "input") or {}}
    if hasattr(block, "model_dump"):
        return dict(block.model_dump())
    return dict(block) if isinstance(block, Mapping) else {"type": str(kind)}


def _field(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, Mapping) else getattr(block, name, None)


def anthropic(client: Any, model: str, *, max_tokens: int = 16000, max_steps: int = 8, system: str = "",
              retries: int = 4, media: Collection[str] | None = None, retry_truncated: bool = True,
              extra: Mapping[str, Any] | None = None) -> Participant:
    """An LLM participant using an ``anthropic.Anthropic()`` client.

    The model is shown one tool list for the whole turn: the tools legal when the turn starts. A call to one that is no
    longer legal is refused with the reason, each tool result names the offered tools not available any more, and a
    tool that becomes legal during the turn is added. So every call of a turn sends the same prefix, and two
    prompt-cache breakpoints — the system prompt (``system`` and the brief, cached after the tools) and the latest
    message — let each call read the one before it from the cache. A breakpoint is placed only where a later call can
    read it: the latest message only in a turn that may make several calls (some tool other than end_turn leaves the
    turn open), and the system prompt in such a turn or when the agent's turn opens with the same tools and system
    prompt as its previous one — and only once its prefix is long enough for any model to cache (about 512 tokens).
    When the agent has no action it could take, the model is not called and the turn ends.

    Files the agent receives are sent as image and document blocks after the text (``media``: the attachment types
    sent as content, default image, pdf and text; ``media=()`` for a text-only model, which reads each file's
    reference — its caption and alt text — in the text only). See :mod:`fg_env.assets.multimodal`.

    ``extra`` holds more request fields sent with every call, such as ``{"temperature": 0}``. Pass the sync
    client: an async client fails the run saying so.

    Each request is sent with a ``timeout`` of the time left in the turn (at most 10 minutes), so none outlives it.
    Rate limits, timeouts, overload and server errors are retried ``retries`` times with backoff (honouring
    ``retry-after``, never past the turn's time limit: once the turn is over no call is made); if a call still fails,
    the turn is forfeited, counted in ``stats["forfeits"]`` and reported in
    the run's diagnostics. Any other error — a rejected API key, an unknown model, a bad request, a client that does
    not fit — fails the run at once, naming the agent, the provider's error and the fix. A reply the provider refused
    ends the turn and counts in ``stats["refusals"]``. Real token usage lands in the run's statistics and in
    ``participant.usage``.

    A reply cut off at ``max_tokens`` (default 16000: room for a model that thinks before it answers) counts in
    ``stats["truncated"]``; when it called no tool, the model is asked once for a short tool call
    (``retry_truncated=False`` ends the turn instead); its tool calls, whose arguments may be cut off, are not made.
    Any other reply that calls no tool is reminded once of the tools offered; a reply that still calls none ends the
    turn, and in a turn that took no action counts in ``stats["no_tool_replies"]`` (with an action open, a failed
    turn). A turn that makes all ``max_steps`` model calls ends there and counts in ``stats["out_of_steps"]``. Calls
    left in a reply after one of them ended the turn are not made. In a stage where the agent must act, the
    participant never ends the turn itself: the engine closes it and reports that the agent did not act.
    """
    return _Anthropic(client, model, max_tokens, max_steps, system, retries,
                      media_set(media, ANTHROPIC_MEDIA, ANTHROPIC_MEDIA), retry_truncated, extra)


class _OpenAI(_LLMParticipant):
    CLIENT = "openai.OpenAI()"
    CALL = "client.chat.completions.create"
    PROVIDER = "openai"

    def __init__(self, client: Any, model: str, max_tokens: int | None, reasoning_effort: str | None,
                 max_steps: int, system: str, retries: int, media: frozenset, retry_truncated: bool,
                 extra: Mapping[str, Any] | None):
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens
                                       < 1):
            raise ValueError(f"max_tokens must be a whole number ≥ 1 (or None), got {max_tokens!r}")
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            raise ValueError(f"reasoning_effort must be text such as 'low' (or None), got {reasoning_effort!r}")
        #: Sent only when set, so a client that does not know a field never receives it.
        self.options: dict[str, Any] = {key: value for key, value in (("max_completion_tokens", max_tokens),
                                                                      ("reasoning_effort", reasoning_effort))
                                        if value is not None}
        sent = ["model", "messages", "tools", "timeout", *self.options,
                *(["max_tokens"] if max_tokens is not None else [])]
        super().__init__(client, model, max_steps, system, retries, media, retry_truncated, _extra(extra, sent))
        cap = max_tokens if max_tokens is not None else self.extra.get("max_tokens")  # the older field, via extra
        self.output_cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else None

    def _turn(self, wake: Wake) -> None:
        parts = openai_parts(wake.attachments, self.media) if self.media else []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": (self.system + "\n\n" if self.system else "") + wake.brief},
            {"role": "user", "content": [{"type": "text", "text": wake.update}, *parts] if parts else wake.update},
        ]
        offered = _TurnTools(wake, "openai")
        asked = False
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = offered.definitions
            response = self._create(wake, lambda: self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, timeout=request_timeout(wake.time_left),  # noqa: B023 — called within this iteration
                **self.options, **self.extra),
                _tokens(tools, messages))
            choice = response.choices[0]
            finish = getattr(choice, "finish_reason", None)
            message = choice.message
            if getattr(message, "refusal", None) or finish == "content_filter":
                self._record(wake, refusals=1)
                return  # asking again after a refusal only invites another
            truncated = finish == "length"
            if truncated:
                self._record(wake, truncated=1)
            calls = [] if truncated else list(getattr(message, "tool_calls", None)
                                              or [])  # cut-off arguments are not made
            assistant: dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None) or ""}
            if not calls:
                follow = self._follow_up(wake, truncated, asked)
                if follow is None:
                    return
                asked = True
                messages += [assistant, {"role": "user", "content": follow}]
                continue
            assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                       for c in calls]
            messages.append(assistant)
            files: list[dict[str, Any]] = []
            for c in calls:
                raw = c.function.arguments
                args, broken = parse_arguments(raw or "{}") if isinstance(raw, str) or raw is None else (raw, "")
                if broken:
                    args = raw  # not readable: the engine refuses it, saying why, and counts it invalid
                result = self._dispatch(wake, c.function.name, args)
                text = _NOT_RUN if result is None else result.text
                if result is not None and self.media and result.attachments:
                    files += openai_parts(result.attachments, self.media)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": text})
            messages[-1]["content"] += offered.changes(wake)
            if files:  # tool messages carry text only: the files follow in one user message
                messages.append({"role": "user",
                                 "content": [{"type": "text", "text": "Files from the tool results above:"},
                                             *files]})
        self._out_of_steps(wake)

    @staticmethod
    def _empty(response: Any) -> bool:
        choices = getattr(response, "choices", None)
        return not choices or getattr(choices[0], "finish_reason", None) == "error"


def openai(client: Any, model: str, *, max_tokens: int | None = None, reasoning_effort: str | None = None,
           max_steps: int = 8, system: str = "", retries: int = 4, media: Collection[str] | None = None,
           retry_truncated: bool = True, extra: Mapping[str, Any] | None = None) -> Participant:
    """An LLM participant using an ``openai.OpenAI()``-compatible client (chat completions + tools).

    ``max_tokens`` caps each reply (sent as ``max_completion_tokens``) and ``reasoning_effort`` (``"low"``,
    ``"medium"``, ``"high"``) is passed on to reasoning models; each is sent only when given. A server that knows only
    the older ``max_tokens`` field takes ``extra={"max_tokens": 1024}`` instead. A response with no choices in it, or
    with ``finish_reason`` ``error`` (OpenRouter sends both now and then), is retried like an overload. Retries,
    failures, refusals (a ``refusal`` message or ``finish_reason`` ``content_filter``), ``extra``, usage accounting,
    truncated replies (``finish_reason`` ``length``), ``retry_truncated`` and the one tool list per turn work as for
    :func:`anthropic`; arguments that are not a JSON object (or not valid JSON, which the refusal says) are refused and
    counted as invalid calls. Files are sent as ``image_url`` data URLs, ``file`` and ``input_audio`` parts (``media``:
    default image, pdf, audio and text; ``()`` for text only); files from tool results follow the tool messages in one
    user message.
    """
    return _OpenAI(client, model, max_tokens, reasoning_effort, max_steps, system, retries,
                   media_set(media, OPENAI_MEDIA, OPENAI_MEDIA), retry_truncated, extra)
