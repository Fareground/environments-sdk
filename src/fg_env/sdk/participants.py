"""Participants: whoever takes the turns. Anything callable with a :class:`Wake` works.

Built in:

* ``"random"`` — a seeded agent that takes random legal actions with valid arguments.
* ``"idle"`` — ends every turn without acting.
* ``"policy:<name>"`` (or just the policy's name) — a coded policy declared in the contract.
* :func:`anthropic` / :func:`openai` — LLM participants driving the turn with tool calls,
  given your own client object (no SDK dependency is imposed).
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Collection, Dict, List, Mapping, Optional, Union

from .assets.multimodal import ANTHROPIC_MEDIA, OPENAI_MEDIA, anthropic_parts, media_set, openai_parts
from .expr import ExprError, compile_expr, resolve, truthy
from .session import Wake

if TYPE_CHECKING:
    from .contract import Contract

__all__ = ["Participant", "RandomAgent", "Idle", "PolicyAgent", "anthropic", "openai", "replay", "resolve_participant"]

Participant = Callable[[Wake], Any]


def _seed_for(base: int, wake: Wake) -> int:
    import hashlib

    digest = hashlib.sha256(repr((base, wake.entity_id, wake.round, wake.stage)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class RandomAgent:
    """Takes up to ``actions`` random legal actions per turn with valid random arguments."""

    #: Coded participants are fast; running them in order avoids thread overhead.
    concurrent = False

    def __init__(self, seed: int = 0, actions: int = 1, pass_rate: float = 0.0):
        self.seed = seed
        self.actions = actions
        self.pass_rate = pass_rate

    def __call__(self, wake: Wake) -> None:
        rng = random.Random(_seed_for(self.seed, wake))
        for _ in range(self.actions):
            acts = [t for t in wake.tools if t.kind == "act"]
            if not acts or wake.done or rng.random() < self.pass_rate:
                break
            tool = rng.choice(acts)
            result = wake.call(tool.name, sample_args(tool.input_schema, rng))
            if result.ended:
                return
        if not wake.done:
            wake.end()

    def __repr__(self) -> str:
        return f"RandomAgent(seed={self.seed})"


def sample_args(schema: Mapping[str, Any], rng: random.Random) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    for name, prop in (schema.get("properties") or {}).items():
        if "enum" in prop:
            if prop["enum"]:
                args[name] = rng.choice(prop["enum"])
            continue
        kind = prop.get("type")
        if kind in ("integer", "number"):
            low = prop.get("minimum", 0)
            high = prop.get("maximum", max(low, 0) + 10)
            if high < low:
                continue
            step = prop.get("multipleOf")
            if step:
                first, last = math.ceil(low / step), math.floor(high / step)
                if last >= first:
                    args[name] = rng.randint(first, last) * step
                continue
            args[name] = rng.randint(int(low), int(high)) if kind == "integer" else round(rng.uniform(low, high), 2)
        elif kind == "boolean":
            args[name] = rng.random() < 0.5
        elif kind == "string":
            args[name] = "ok"
        elif kind == "array":
            items = _sample_list(prop, rng)
            if items is not None:
                args[name] = items
    return args


def _sample_list(prop: Mapping[str, Any], rng: random.Random) -> Optional[List[Any]]:
    item = prop.get("items") or {}
    low = int(prop.get("minItems", 0))
    high = max(low, min(int(prop.get("maxItems", low + 3)), low + 3))
    unique = bool(prop.get("uniqueItems"))
    pool = item.get("enum")
    if pool is not None:
        if unique:
            if low > len(pool):
                return None
            return rng.sample(list(pool), rng.randint(low, min(high, len(pool))))
        return [rng.choice(pool) for _ in range(rng.randint(low, high))] if pool else ([] if low == 0 else None)
    out: List[Any] = []
    for _ in range(rng.randint(low, high)):
        value = sample_args({"properties": {"x": item}}, rng).get("x")
        if value is not None and not (unique and value in out):
            out.append(value)
    return out if len(out) >= low else None


class Idle:
    """Never acts."""

    #: Coded participants are fast; running them in order avoids thread overhead.
    concurrent = False

    def __call__(self, wake: Wake) -> None:
        wake.end()

    def __repr__(self) -> str:
        return "Idle()"


class PolicyAgent:
    """Runs a coded policy from the contract's ``policies`` section: the first rule whose condition
    holds, whose action is legal and whose arguments are valid is taken."""

    concurrent = False

    def __init__(self, contract: "Contract", name: str, seed: int = 0):
        if name not in contract.policies:
            raise ValueError(f"no policy '{name}' in the contract (policies: {', '.join(contract.policies) or 'none'})")
        self.name = name
        self.spec = contract.policies[name]
        self.seed = seed

    def __call__(self, wake: Wake) -> None:
        rng = random.Random(_seed_for(self.seed, wake))
        turn = wake._turn
        while not wake.done:
            acted = False
            for index, rule in enumerate(self.spec.rules):
                path = f"policies.{self.name}.rules[{index}]"
                scope = turn.env.world.scope(actor=turn.actor)
                if rule.each is None:
                    outcome = self._try(wake, rule, scope, rng, path)
                    if outcome == "passed":
                        return
                    if outcome == "acted":
                        acted = True
                        break
                    continue
                for position, item in enumerate(self._items(turn, rule.each, scope, path)):
                    if wake.done:
                        return
                    outcome = self._try(wake, rule, scope.child(it=item, i=position), rng, path)
                    if outcome == "passed":
                        return
                    acted = acted or outcome == "acted"
                if acted:
                    break
            if not acted or not self.spec.repeat:
                break
        if not wake.done:
            wake.end()

    @staticmethod
    def _items(turn: Any, each: str, scope: Any, path: str) -> List[Any]:
        from .errors import RunError

        world = turn.env.world
        try:
            items = world.entities_of(each) if each in turn.env.contract.types else compile_expr(each)(scope)
        except ExprError as exc:
            raise RunError(str(exc), f"{path}.each") from None
        return list(items or [])

    def _try(self, wake: Wake, rule: Any, scope: Any, rng: random.Random, path: str) -> str:
        """Try one rule: "acted", "passed" (the turn ends), or "skipped"."""
        turn = wake._turn
        try:
            if rule.when is not None and not truthy(compile_expr(rule.when)(scope)):
                return "skipped"
            if rule.chance is not None:
                p = compile_expr(rule.chance)(scope) if isinstance(rule.chance, str) else rule.chance
                if isinstance(p, bool) or not isinstance(p, (int, float)):
                    raise ExprError(f"chance must be a number, got {p!r}", str(rule.chance))
                if rng.random() >= p:
                    return "skipped"
            if rule.do == "pass":
                wake.end()
                return "passed"
            args = resolve(rule.with_, scope)
        except ExprError as exc:
            from .errors import RunError

            raise RunError(str(exc), path) from None
        args = {k: (v.id if hasattr(v, "entity_type") else v) for k, v in args.items()}
        with turn.env._lock:  # legality without building tool schemas: coded crowds never read them
            legal = not wake.done and rule.do in turn._legal()
        if not legal:
            return "skipped"
        with turn.env._lock:
            _, problem = turn.env.actions.validate(turn.actor, rule.do, args)
        if problem:
            return "skipped"  # this rule does not fit right now; try the next one
        return "acted" if wake.call(rule.do, args).ok else "skipped"

    def __repr__(self) -> str:
        return f"PolicyAgent({self.name!r})"


def resolve_participant(value: Any, contract: "Contract", seed: int) -> Participant:
    if callable(value):
        return value
    if isinstance(value, str):
        if value == "random":
            return RandomAgent(seed)
        if value == "idle":
            return Idle()
        name = value[len("policy:"):] if value.startswith("policy:") else value
        if name in contract.policies:
            return PolicyAgent(contract, name, seed)
        from .game.algorithms.participants import algorithm_participant

        algorithm = algorithm_participant(value, contract, seed)
        if algorithm is not None:
            return algorithm
    raise ValueError(
        f"unknown participant {value!r}: use a callable, 'random', 'idle', 'policy:<name>', or a game algorithm: "
        f"'mcts:<simulations>', 'ismcts:<simulations>', 'minimax[:<depth>]', 'cfr:<policy.json>' or "
        f"'cfr:<iterations>' (policies: {', '.join(contract.policies) or 'none'})"
    )


def replay(recording: Any, fallback: Any = None) -> Participant:
    """A participant that plays a recorded run's steps again, turn by turn, checking every wake against the
    recording (``recording``: a result with exposures, its dict, a saved file, or a trace); the run it plays in must
    record exposures. On the first difference the run fails with the divergence, or — given ``fallback`` — that
    participant plays on. Usually you want ``fg_env.trace(recording).replay(contract)``, which also replays the host
    answers and compares the outcome."""
    from .trace.rerun import Replayer

    return Replayer(recording, fallback)


# ---------------------------------------------------------------------------
# LLM participants
# ---------------------------------------------------------------------------


class _LLMUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.retries = 0
        self.forfeits = 0

    def to_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


#: HTTP statuses worth retrying: timeouts, conflicts, rate limits, overload and server errors.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_RETRY_NAMES = ("RateLimit", "Timeout", "Connection", "Overloaded", "InternalServer", "ServiceUnavailable")
_MAX_BACKOFF_SECONDS = 60.0
_NUDGE = "Act only by calling your tools. When you have nothing more to do, call end_turn."


def _retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _RETRY_STATUSES
    return any(part in type(exc).__name__ for part in _RETRY_NAMES)


def _retry_after(exc: BaseException) -> Optional[float]:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = float(headers.get("retry-after")) if headers is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


class _ProviderFailed(Exception):
    """A provider call still failed after its retries."""


class _LLMParticipant:
    """The shared tool loop: retries, usage accounting, the error policy."""

    def __init__(self, client: Any, model: str, max_steps: int, system: str, retries: int, on_error: str,
                 media: frozenset = frozenset()):
        if on_error not in ("fail", "end_turn"):
            raise ValueError(f"on_error must be 'fail' or 'end_turn', got {on_error!r}")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError(f"retries must be a whole number ≥ 0, got {retries!r}")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError(f"max_steps must be a whole number ≥ 1, got {max_steps!r}")
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.system = system
        self.retries = retries
        self.on_error = on_error
        #: Attachment types sent as real content; the rest reach the model as their text references only.
        self.media = media
        self.usage = _LLMUsage()
        self._usage_lock = threading.Lock()

    def __call__(self, wake: Wake) -> None:
        try:
            self._turn(wake)
        except _ProviderFailed as failure:
            cause = failure.__cause__ or failure
            if self.on_error == "fail":
                raise cause
            self._record(wake, forfeits=1)
        if not wake.done:
            wake.end()

    def _turn(self, wake: Wake) -> None:
        raise NotImplementedError

    def _create(self, wake: Wake, request: Callable[[], Any]) -> Any:
        for attempt in range(self.retries + 1):
            try:
                return request()
            except Exception as exc:
                if attempt >= self.retries or not _retryable(exc):
                    raise _ProviderFailed(f"{type(exc).__name__}: {exc}") from exc
                self._record(wake, llm_retries=1)
                delay = _retry_after(exc)
                time.sleep(min(_MAX_BACKOFF_SECONDS, delay if delay is not None else 2.0 ** attempt))
        raise AssertionError("unreachable")

    def _record(self, wake: Wake, **counts: int) -> None:
        wake.record_usage(**counts)
        mapping = {"llm_calls": "calls", "llm_retries": "retries"}
        with self._usage_lock:
            for name, value in counts.items():
                attr_name = mapping.get(name, name)
                setattr(self.usage, attr_name, getattr(self.usage, attr_name) + value)


class _Anthropic(_LLMParticipant):
    def __init__(self, client: Any, model: str, max_tokens: int, max_steps: int, system: str, retries: int,
                 on_error: str, media: frozenset):
        super().__init__(client, model, max_steps, system, retries, on_error, media)
        self.max_tokens = max_tokens

    def _turn(self, wake: Wake) -> None:
        system = [{"type": "text", "text": (self.system + "\n\n" if self.system else "") + wake.brief,
                   "cache_control": {"type": "ephemeral"}}]
        parts = anthropic_parts(wake.attachments, self.media) if self.media else []
        opening: Any = [{"type": "text", "text": wake.update}, *parts] if parts else wake.update
        messages: List[Dict[str, Any]] = [{"role": "user", "content": opening}]
        nudged = False
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = wake.tools_for("anthropic")
            response = self._create(wake, lambda: self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system, tools=tools, messages=messages))
            self._count(wake, getattr(response, "usage", None))
            content = [_block_dict(b) for b in (getattr(response, "content", None) or [])]
            calls = [block for block in content if block.get("type") == "tool_use"]
            if not content:
                return
            messages.append({"role": "assistant", "content": content})
            if not calls:
                if nudged or wake.done:
                    return
                nudged = True
                messages.append({"role": "user", "content": _NUDGE})
                continue
            results = []
            for block in calls:
                args = block.get("input")
                result = wake.call(str(block.get("name")), args if isinstance(args, dict) else None)
                files = anthropic_parts(result.attachments, self.media) if self.media and result.attachments else []
                reply: Any = [{"type": "text", "text": result.text}, *files] if files else result.text
                results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": reply,
                                "is_error": not result.ok})
            messages.append({"role": "user", "content": results})

    def _count(self, wake: Wake, usage: Any) -> None:
        def number(name: str) -> int:
            value = getattr(usage, name, 0) if usage is not None else 0
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        self._record(wake, llm_calls=1, input_tokens=number("input_tokens"), output_tokens=number("output_tokens"),
                     cache_read_tokens=number("cache_read_input_tokens"),
                     cache_write_tokens=number("cache_creation_input_tokens"))


def _block_dict(block: Any) -> Dict[str, Any]:
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


def anthropic(client: Any, model: str, *, max_tokens: int = 1024, max_steps: int = 8, system: str = "",
              retries: int = 4, on_error: str = "fail", media: Optional[Collection[str]] = None) -> Participant:
    """An LLM participant using an ``anthropic.Anthropic()`` client. The brief is prompt-cached.

    Files the agent receives are sent as image and document blocks after the text (``media``: the attachment types
    sent as content, default image, pdf and text; ``media=()`` for a text-only model, which reads each file's
    reference — its caption and alt text — in the text only). See :mod:`fg_env.sdk.assets.multimodal`.

    Rate limits, timeouts, overload and server errors are retried ``retries`` times with backoff
    (honouring ``retry-after``). If a call still fails, ``on_error="fail"`` fails the run with that
    error and ``"end_turn"`` forfeits the turn and counts it in ``stats["forfeits"]``. Real token
    usage lands in the run's statistics and in ``participant.usage``.
    """
    return _Anthropic(client, model, max_tokens, max_steps, system, retries, on_error,
                      media_set(media, ANTHROPIC_MEDIA, ANTHROPIC_MEDIA))


class _OpenAI(_LLMParticipant):
    def _turn(self, wake: Wake) -> None:
        parts = openai_parts(wake.attachments, self.media) if self.media else []
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": (self.system + "\n\n" if self.system else "") + wake.brief},
            {"role": "user", "content": [{"type": "text", "text": wake.update}, *parts] if parts else wake.update},
        ]
        nudged = False
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = wake.tools_for("openai")
            response = self._create(wake, lambda: self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools))
            self._count(wake, getattr(response, "usage", None))
            choices = getattr(response, "choices", None) or []
            if not choices:
                return
            message = choices[0].message
            calls = list(getattr(message, "tool_calls", None) or [])
            assistant: Dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None) or ""}
            if calls:
                assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                           for c in calls]
            messages.append(assistant)
            if not calls:
                if nudged or wake.done:
                    return
                nudged = True
                messages.append({"role": "user", "content": _NUDGE})
                continue
            files: List[Dict[str, Any]] = []
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = None
                if isinstance(args, dict):
                    result = wake.call(c.function.name, args)
                    text = result.text
                    if self.media and result.attachments:
                        files += openai_parts(result.attachments, self.media)
                else:
                    text = "The arguments were not a JSON object of named values; call the tool again with valid JSON."
                messages.append({"role": "tool", "tool_call_id": c.id, "content": text})
            if files:  # tool messages carry text only: the files follow in one user message
                messages.append({"role": "user", "content": [{"type": "text", "text": "Files from the tool results above:"},
                                                             *files]})

    def _count(self, wake: Wake, usage: Any) -> None:
        def number(owner: Any, name: str) -> int:
            value = getattr(owner, name, 0) if owner is not None else 0
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        cached = number(getattr(usage, "prompt_tokens_details", None), "cached_tokens")
        self._record(wake, llm_calls=1, input_tokens=number(usage, "prompt_tokens"),
                     output_tokens=number(usage, "completion_tokens"), cache_read_tokens=cached)


def openai(client: Any, model: str, *, max_steps: int = 8, system: str = "", retries: int = 4,
           on_error: str = "fail", media: Optional[Collection[str]] = None) -> Participant:
    """An LLM participant using an ``openai.OpenAI()``-compatible client (chat completions + tools).

    Retries, ``on_error`` and usage accounting work as for :func:`anthropic`. Files are sent as ``image_url`` data
    URLs, ``file`` and ``input_audio`` parts (``media``: default image, pdf, audio and text; ``()`` for text only);
    files from tool results follow the tool messages in one user message.
    """
    return _OpenAI(client, model, max_steps, system, retries, on_error, media_set(media, OPENAI_MEDIA, OPENAI_MEDIA))


ParticipantsArg = Union[None, Participant, str, Mapping[str, Any]]
