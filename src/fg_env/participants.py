"""Participants: whoever takes the turns. Anything callable with a :class:`~fg_env.Wake` works.

``"random"``, ``"idle"`` and ``"policy:<name>"`` name built-in participants; :func:`anthropic` and :func:`openai`
drive a turn with your own LLM client; :func:`replay` plays a recorded run back.
"""
from __future__ import annotations

import importlib
import inspect
import json
import math
import os
import random
import threading
import time
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Callable, Collection, Dict, List, Mapping, Optional, Union

from .assets.multimodal import ANTHROPIC_MEDIA, OPENAI_MEDIA, anthropic_parts, media_set, openai_parts
from .errors import ContractError, Issue, RunError
from .probability import is_probability
from .expr import ExprError, compile_expr, resolve, truthy
from .seeds import LazyStream
from .session import END_TURN, ToolResult, Wake

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
            result = wake.call(tool.name, _fill_dependent(wake, tool.name, sample_args(tool.input_schema, rng), rng))
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


def _fill_dependent(wake: Wake, tool: str, args: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """``args`` with each choice that depends on earlier arguments (``where: $it.id != $params.a.id``) drawn from
    the entities that qualify given them: a schema can only list every candidate."""
    from .errors import RunError

    turn = wake._turn
    actions = turn.env.actions
    name, own = tool, args
    if tool in actions.groups:  # a shared tool: its `action` argument names the action
        name, own, problem = actions.route(tool, args, ())
        if problem:
            return args
    if name not in turn.env.contract.actions:
        return args
    with turn.env._lock:
        try:
            filled = actions.fill_dependent(turn.actor, name, own, lambda found: rng.choice(found) if found else None)
        except RunError:
            return args  # the call reports the broken rule at its path
    return {**filled, "action": args["action"]} if name != tool else filled


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
    #: Before a rule acts, also evaluate the later rules whose action is legal, so a broken rule that an earlier one
    #: always beats is still reported. Check's smoke play sets it; the policy acts the same either way.
    _probe_later = False

    def __init__(self, contract: "Contract", name: str, seed: int = 0):
        if name not in contract.policies:
            raise ValueError(f"no policy '{name}' in the contract (policies: {', '.join(contract.policies) or 'none'})")
        self.name = name
        self.spec = contract.policies[name]
        self.seed = seed

    def __call__(self, wake: Wake) -> None:
        rng: Any = LazyStream(lambda: random.Random(_seed_for(self.seed, wake)))  # only `chance` rules draw
        turn = wake._turn
        while not wake.done:
            acted = False
            for index, rule in enumerate(self.spec.rules):
                path = f"policies.{self.name}.rules[{index}]"
                scope = turn.env.world.scope(actor=turn.actor, viewer=turn.actor)
                if rule.each is None:
                    outcome = self._try(wake, index, scope, rng)
                    if outcome == "passed":
                        return
                    if outcome == "acted":
                        acted = True
                        break
                    continue
                for position, item in enumerate(self._items(turn, rule.each, scope, path)):
                    if wake.done:
                        return
                    outcome = self._try(wake, index, scope.child(it=item, i=position), rng)
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
        world = turn.env.world
        try:
            items = world.entities_of(each) if each in turn.env.contract.types else compile_expr(each)(scope)
        except ExprError as exc:
            raise RunError(str(exc), f"{path}.each") from None
        return list(items or [])

    def _try(self, wake: Wake, index: int, scope: Any, rng: Any) -> str:
        """Try one rule: "acted", "passed" (the turn ends), or "skipped"."""
        turn, rule, path = wake._turn, self.spec.rules[index], f"policies.{self.name}.rules[{index}]"
        try:
            if rule.when is not None and not truthy(compile_expr(rule.when)(scope)):
                return "skipped"
            if rule.chance is not None:
                p = compile_expr(rule.chance)(scope) if isinstance(rule.chance, str) else rule.chance
                if not is_probability(p):
                    raise ExprError(f"chance must be a number from 0 to 1, got {p!r}", str(rule.chance))
                if rng.random() >= p:
                    return "skipped"
            if rule.do == "pass":
                if self._probe_later:
                    self._probe(turn, index)
                wake.end()
                return "passed"
            args = resolve(rule.with_, scope)
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        args = {k: _as_ids(v) for k, v in args.items()}
        with turn.env._lock:  # legality without building tool schemas: coded crowds never read them
            legal = not wake.done and turn._allows(rule.do)
        if not legal:
            return "skipped"
        with turn.env._lock:
            _, problem = turn.env.actions.validate(turn.actor, rule.do, args)
        if problem is None:
            if self._probe_later:
                self._probe(turn, index)
            result = wake.call(rule.do, args)
            if result.ok:
                turn.env.diagnosis.policy_rule(path)
                return "acted"
            problem = result.text
        turn.env.diagnosis.policy_rule(path, problem)
        return "skipped"  # this rule does not fit right now; try the next one

    def _probe(self, turn: Any, index: int) -> None:
        """Evaluate each rule after ``index`` whose action is legal now, as a turn would reach it — `when`, then
        `chance` and `with` if it holds — for the first of its `each` items. Nothing acts, and draws come from a stream
        of their own."""
        scope = turn.env.world.scope(actor=turn.actor, viewer=turn.actor)
        with turn.env._lock:
            legal = set(turn._legal()) | {"pass"}
        with turn.env.world.drawing_from(random.Random(0)):
            for later in range(index + 1, len(self.spec.rules)):
                rule, path = self.spec.rules[later], f"policies.{self.name}.rules[{later}]"
                if rule.do not in legal:
                    continue
                items = [scope] if rule.each is None else [
                    scope.child(it=item, i=0) for item in self._items(turn, rule.each, scope, path)[:1]]
                for here in items:
                    try:
                        if rule.when is None or truthy(compile_expr(rule.when)(here)):
                            if isinstance(rule.chance, str):
                                compile_expr(rule.chance)(here)
                            resolve(rule.with_, here)
                    except ExprError as exc:
                        raise RunError(f"{exc} (evaluated while rules[{index}] acted first; a turn that reaches this "
                                       "rule fails the same way)", path) from None

    def __repr__(self) -> str:
        return f"PolicyAgent({self.name!r})"


def _as_ids(value: Any) -> Any:
    """A policy argument with entities (alone or in a list, e.g. a ranking from $top) given as their ids."""
    if isinstance(value, list):
        return [_as_ids(item) for item in value]
    return value.id if hasattr(value, "entity_type") else value


def resolve_participant(value: Any, contract: "Contract", seed: int, path: str = "participants") -> Participant:
    """The participant ``value`` names; an unknown name raises :class:`~fg_env.ContractError` at ``path``."""
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
        provider, sep, model = value.partition(":")
        if sep and provider in _PROVIDERS:
            return _on_official_client(provider, model)
        from .game.algorithms.participants import algorithm_participant

        algorithm = algorithm_participant(value, contract, seed)
        if algorithm is not None:
            return algorithm
    named = ["random", "idle", *(f"policy:{name}" for name in contract.policies)]
    hint = get_close_matches(str(value), named, n=1)
    raise ContractError([Issue(path, f"unknown participant {value!r}", (f"did you mean '{hint[0]}'? " if hint else "")
                               + "use a callable, 'random', 'idle', 'policy:<name>', 'anthropic:<model>', 'openai:<model>', "
                               "or a game algorithm: 'mcts:<simulations>', 'ismcts:<simulations>', 'minimax[:<depth>]', "
                               f"'cfr:<policy.json>' or 'cfr:<iterations>' (policies: {', '.join(contract.policies) or 'none'})")],
                        title="participants are invalid")


#: ``<provider>:<model>`` participants: the official client's class, and the variable holding its API key.
_PROVIDERS = {"anthropic": ("Anthropic", "ANTHROPIC_API_KEY"), "openai": ("OpenAI", "OPENAI_API_KEY")}


def _on_official_client(provider: str, model: str) -> Participant:
    """The LLM participant ``<provider>:<model>`` names, on the provider's client made from the environment's key."""
    make = anthropic if provider == "anthropic" else openai
    return make(official_client(provider, model), model)


def official_client(provider: str, model: str) -> Any:
    """The official ``anthropic`` or ``openai`` client for ``<provider>:<model>``, made from the environment's key."""
    client_class, key = _PROVIDERS[provider]
    if not model:
        raise ValueError(f"'{provider}:' names no model: use '{provider}:<model>'")
    if not os.environ.get(key):
        raise ValueError(f"'{provider}:{model}' needs an API key: set {key} in the environment")
    try:
        module = importlib.import_module(provider)
    except ImportError:
        raise ValueError(f"'{provider}:{model}' needs the {provider} package: pip install {provider}") from None
    return getattr(module, client_class)()


def replay(recording: Any, fallback: Any = None) -> Participant:
    """A participant that plays a recorded run's steps again, turn by turn, checking every wake against the
    recording (``recording``: a result with exposures, its dict, a saved file, or a trace); the run it plays in must
    record exposures. On the first difference the run fails with the divergence, or — given ``fallback`` — that
    participant plays on. Usually you want ``fg_env.analysis.trace(recording).replay(contract)``, which also replays the host
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
        self.truncated = 0
        self.refusals = 0

    def to_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


#: HTTP statuses worth retrying: timeouts, conflicts, rate limits, overload and server errors.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_RETRY_NAMES = ("RateLimit", "Timeout", "Connection", "Overloaded", "InternalServer", "ServiceUnavailable")
_MAX_BACKOFF_SECONDS = 60.0
#: What a reply cut off at the output limit is asked, once, when ``retry_truncated`` is on.
_TRUNCATED = ("Your reply was cut off at the output limit before it called a tool. Answer now with a tool call; "
              "keep your reasoning short.")
#: What a leftover call in a reply gets once the turn has ended (it is not sent to the engine).
_NOT_RUN = "Not done: your turn was already over."


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


def _add_user_text(messages: List[Dict[str, Any]], text: str) -> None:
    """Add ``text`` to the user message the conversation ends with (a reply with no content keeps no assistant turn)."""
    last = messages[-1]
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    last["content"] = blocks + [{"type": "text", "text": text}]


def _retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _RETRY_STATUSES
    return any(part in type(exc).__name__ for part in _RETRY_NAMES)


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


def _retry_after(exc: BaseException) -> Optional[float]:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = float(headers.get("retry-after")) if headers is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and value >= 0 else None


class _Forfeit(Exception):
    """A provider call still failed after its retries: the turn is lost, not the run."""


def _extra(extra: Optional[Mapping[str, Any]], sent: Collection[str]) -> Dict[str, Any]:
    """The ``extra`` request fields, refusing any of the fields the participant sends itself (``sent``)."""
    if extra is None:
        return {}
    if not isinstance(extra, Mapping):
        raise ValueError(f"extra must be a mapping of request fields, such as {{'temperature': 0}}; got {extra!r}")
    clash = [key for key in extra if key in sent]
    if clash:
        raise ValueError(f"extra cannot set {', '.join(map(repr, clash))}: the participant sends it itself (the model, "
                         "system prompt, max_tokens and reasoning_effort are its own arguments)")
    return dict(extra)


class _LLMParticipant:
    """The shared tool loop: retries, usage accounting, failing loudly on errors retrying cannot fix."""

    #: The provider's sync client, and the call the loop makes on it (named in error messages).
    CLIENT = ""
    CALL = ""

    def __init__(self, client: Any, model: str, max_steps: int, system: str, retries: int,
                 media: frozenset = frozenset(), retry_truncated: bool = True, extra: Optional[Dict[str, Any]] = None):
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

    def __call__(self, wake: Wake) -> None:
        try:
            self._turn(wake)
        except _Forfeit:
            self._record(wake, forfeits=1)
        if not wake.done and _may_end(wake):
            wake.end()  # in a must-act stage the engine closes the turn instead, and reports that the agent did not act

    def _turn(self, wake: Wake) -> None:
        raise NotImplementedError

    def _follow_up(self, wake: Wake, truncated: bool, asked: bool) -> Optional[str]:
        """What to tell a model whose reply called no tool, or None to end the loop: one follow-up per turn — the
        short retry after a truncated reply (when ``retry_truncated``), else the nudge naming the tools offered."""
        if asked or wake.done or (truncated and not self.retry_truncated):
            return None
        return _TRUNCATED if truncated else _nudge(wake)

    @staticmethod
    def _dispatch(wake: Wake, name: Any, args: Any) -> Optional[ToolResult]:
        """Run one tool call of a reply, or None when an earlier call of the same reply ended the turn: leftover
        calls are answered without reaching the engine, so they are never counted."""
        if wake.done:
            return None
        return wake.call(name, args)

    def _create(self, wake: Wake, request: Callable[[], Any]) -> Any:
        """One provider call. Rate limits, timeouts, overload and server errors are retried; when the retries run out
        the turn is forfeited. Any other error fails the run: retrying would send the same request again."""
        for attempt in range(self.retries + 1):
            try:
                response = request()
            except Exception as exc:
                if not _retryable(exc):
                    raise self._failure(wake, exc) from exc
                if attempt >= self.retries:
                    raise _Forfeit() from exc
                self._record(wake, llm_retries=1)
                delay = _retry_after(exc)
                time.sleep(min(_MAX_BACKOFF_SECONDS, delay if delay is not None else 2.0 ** attempt))
                continue
            if inspect.isawaitable(response):
                if inspect.iscoroutine(response):
                    response.close()  # never awaited: closed so it does not linger
                raise RunError(f"{self.CALL} returned an awaitable, so this is an async client. Pass the sync client, "
                               f"{self.CLIENT}: simultaneous turns already run in parallel, and `await env.arun(...)` "
                               "keeps your event loop free while the run plays", f"participant:{wake.entity_id}")
            return response
        raise AssertionError("unreachable")

    def _failure(self, wake: Wake, exc: BaseException) -> RunError:
        status = getattr(exc, "status_code", None)
        shown = f"{type(exc).__name__} (HTTP {status})" if isinstance(status, int) else type(exc).__name__
        return RunError(f"{self.CALL} failed with {shown}: {exc}. {_permanent_fix(exc, self.CLIENT, self.model)}",
                        f"participant:{wake.entity_id}")

    def _record(self, wake: Wake, **counts: int) -> None:
        wake.record_usage(**counts)
        mapping = {"llm_calls": "calls", "llm_retries": "retries"}
        with self._usage_lock:
            for name, value in counts.items():
                attr_name = mapping.get(name, name)
                setattr(self.usage, attr_name, getattr(self.usage, attr_name) + value)


class _Anthropic(_LLMParticipant):
    CLIENT = "anthropic.Anthropic()"
    CALL = "client.messages.create"

    def __init__(self, client: Any, model: str, max_tokens: int, max_steps: int, system: str, retries: int,
                 media: frozenset, retry_truncated: bool, extra: Optional[Mapping[str, Any]]):
        sent = ("model", "messages", "tools", "system", "max_tokens")
        super().__init__(client, model, max_steps, system, retries, media, retry_truncated, _extra(extra, sent))
        self.max_tokens = max_tokens

    def _turn(self, wake: Wake) -> None:
        system = [{"type": "text", "text": (self.system + "\n\n" if self.system else "") + wake.brief,
                   "cache_control": {"type": "ephemeral"}}]
        parts = anthropic_parts(wake.attachments, self.media) if self.media else []
        opening: Any = [{"type": "text", "text": wake.update}, *parts] if parts else wake.update
        messages: List[Dict[str, Any]] = [{"role": "user", "content": opening}]
        asked = False
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = wake.tools_for("anthropic")
            response = self._create(wake, lambda: self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system, tools=tools, messages=messages,  # noqa: B023 — called within this iteration
                **self.extra))
            self._count(wake, getattr(response, "usage", None))
            if getattr(response, "stop_reason", None) == "refusal":
                self._record(wake, refusals=1)
                return  # asking again after a refusal only invites another
            truncated = getattr(response, "stop_reason", None) == "max_tokens"
            if truncated:
                self._record(wake, truncated=1)
            content = [_block_dict(b) for b in (getattr(response, "content", None) or [])]
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
              retries: int = 4, media: Optional[Collection[str]] = None, retry_truncated: bool = True,
              extra: Optional[Mapping[str, Any]] = None) -> Participant:
    """An LLM participant using an ``anthropic.Anthropic()`` client.

    The system prompt (``system`` and the brief) is marked for prompt caching. Anthropic caches the tools ahead of
    it, and the tools are the actions legal right now with their live choices, so a call reads the cache only when
    the agent is offered the same tools as in an earlier call (typically in a phase it has been in before).

    Files the agent receives are sent as image and document blocks after the text (``media``: the attachment types
    sent as content, default image, pdf and text; ``media=()`` for a text-only model, which reads each file's
    reference — its caption and alt text — in the text only). See :mod:`fg_env.assets.multimodal`.

    ``extra`` holds more request fields sent with every call, such as ``{"temperature": 0}``. Pass the sync
    client: an async client fails the run saying so.

    Rate limits, timeouts, overload and server errors are retried ``retries`` times with backoff (honouring
    ``retry-after``); if a call still fails, the turn is forfeited, counted in ``stats["forfeits"]`` and reported in
    the run's diagnostics. Any other error — a rejected API key, an unknown model, a bad request, a client that does
    not fit — fails the run at once, naming the agent, the provider's error and the fix. A reply the provider refused
    ends the turn and counts in ``stats["refusals"]``. Real token usage lands in the run's statistics and in
    ``participant.usage``.

    A reply cut off at ``max_tokens`` counts in ``stats["truncated"]``; when it called no tool, the model is asked
    once for a short tool call (``retry_truncated=False`` ends the turn instead). Any other reply that calls no tool
    is reminded once of the tools offered. Calls left in a reply after one of them ended the turn are not made. In a
    stage where the agent must act, the participant never ends the turn itself: the engine closes it and reports
    that the agent did not act.
    """
    return _Anthropic(client, model, max_tokens, max_steps, system, retries,
                      media_set(media, ANTHROPIC_MEDIA, ANTHROPIC_MEDIA), retry_truncated, extra)


class _OpenAI(_LLMParticipant):
    CLIENT = "openai.OpenAI()"
    CALL = "client.chat.completions.create"

    def __init__(self, client: Any, model: str, max_tokens: Optional[int], reasoning_effort: Optional[str],
                 max_steps: int, system: str, retries: int, media: frozenset, retry_truncated: bool,
                 extra: Optional[Mapping[str, Any]]):
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1):
            raise ValueError(f"max_tokens must be a whole number ≥ 1 (or None), got {max_tokens!r}")
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            raise ValueError(f"reasoning_effort must be text such as 'low' (or None), got {reasoning_effort!r}")
        #: Sent only when set, so a client that does not know a field never receives it.
        self.options: Dict[str, Any] = {key: value for key, value in (("max_completion_tokens", max_tokens),
                                                                      ("reasoning_effort", reasoning_effort))
                                        if value is not None}
        sent = ["model", "messages", "tools", *self.options, *(["max_tokens"] if max_tokens is not None else [])]
        super().__init__(client, model, max_steps, system, retries, media, retry_truncated, _extra(extra, sent))

    def _turn(self, wake: Wake) -> None:
        parts = openai_parts(wake.attachments, self.media) if self.media else []
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": (self.system + "\n\n" if self.system else "") + wake.brief},
            {"role": "user", "content": [{"type": "text", "text": wake.update}, *parts] if parts else wake.update},
        ]
        asked = False
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = wake.tools_for("openai")
            response = self._create(wake, lambda: self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, **self.options, **self.extra))  # noqa: B023 — called within this iteration
            self._count(wake, getattr(response, "usage", None))
            choices = getattr(response, "choices", None) or []
            if not choices:
                return
            finish = getattr(choices[0], "finish_reason", None)
            message = choices[0].message
            if getattr(message, "refusal", None) or finish == "content_filter":
                self._record(wake, refusals=1)
                return  # asking again after a refusal only invites another
            truncated = finish == "length"
            if truncated:
                self._record(wake, truncated=1)
            calls = list(getattr(message, "tool_calls", None) or [])
            assistant: Dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None) or ""}
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
            files: List[Dict[str, Any]] = []
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = c.function.arguments  # not JSON: the engine refuses it and counts it invalid
                result = self._dispatch(wake, c.function.name, args)
                text = _NOT_RUN if result is None else result.text
                if result is not None and self.media and result.attachments:
                    files += openai_parts(result.attachments, self.media)
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


def openai(client: Any, model: str, *, max_tokens: Optional[int] = None, reasoning_effort: Optional[str] = None,
           max_steps: int = 8, system: str = "", retries: int = 4, media: Optional[Collection[str]] = None,
           retry_truncated: bool = True, extra: Optional[Mapping[str, Any]] = None) -> Participant:
    """An LLM participant using an ``openai.OpenAI()``-compatible client (chat completions + tools).

    ``max_tokens`` caps each reply (sent as ``max_completion_tokens``) and ``reasoning_effort`` (``"low"``,
    ``"medium"``, ``"high"``) is passed on to reasoning models; each is sent only when given. A server that knows only
    the older ``max_tokens`` field takes ``extra={"max_tokens": 1024}`` instead. Retries, failures, refusals (a
    ``refusal`` message or ``finish_reason`` ``content_filter``), ``extra``, usage accounting, truncated replies
    (``finish_reason`` ``length``) and ``retry_truncated`` work as for :func:`anthropic`; arguments that are not a
    JSON object are refused and counted as invalid calls. Files are sent as
    ``image_url`` data URLs, ``file`` and ``input_audio`` parts (``media``: default image, pdf, audio and text; ``()``
    for text only); files from tool results follow the tool messages in one user message.
    """
    return _OpenAI(client, model, max_tokens, reasoning_effort, max_steps, system, retries,
                   media_set(media, OPENAI_MEDIA, OPENAI_MEDIA), retry_truncated, extra)


ParticipantsArg = Union[None, Participant, str, Mapping[str, Any]]
