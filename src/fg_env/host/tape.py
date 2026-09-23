"""The tape: every host answer is recorded in world state the moment it is produced.

A call is identified by what makes it the same call in any replay of the run — the host, the
contract site, the acting entity, the moment (round, clock time, stage) and the call's own
identity (the judged text, the attempt, the tool arguments) — never by call order, so turns
that run concurrently still replay exactly. The answer lives in the world property
``host_tape``, outside the action journal: an answer is a fact about the outside world, kept
even when the action that asked for it is rolled back, so a retry or a replay reads the same
answer. Snapshots carry the tape, so a restored run never asks again.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
from typing import Any, Callable, Dict, Mapping, Optional

from ..errors import FatalRunError
from ..expr import Untrusted
from .hosts import hosts_for
from .protocols import HostError

__all__ = ["TAPE", "MAX_RESPONSE_CHARS", "tape_prop", "plain", "request_key", "consult", "discard", "tape_of"]

TAPE = "host_tape"
#: Largest host answer accepted, as JSON characters.
MAX_RESPONSE_CHARS = 200_000
#: Deepest nesting accepted in a host answer.
_MAX_DEPTH = 32

_LOCK = threading.RLock()


def tape_prop() -> Dict[str, Any]:
    """The world property every host mechanism declares."""
    return {"type": "map", "default": {}, "description": "Host answers recorded for replay (managed by the engine)."}


def plain(value: Any) -> Any:
    """JSON data with provenance markers dropped and entities as ids (for requests and keys)."""
    if isinstance(value, str):
        return str.__str__(value)
    if hasattr(value, "entity_type") and hasattr(value, "id"):
        return value.id
    if isinstance(value, Mapping):
        return {str.__str__(k) if isinstance(k, str) else str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def request_key(world: Any, service: str, site: str, actor: Optional[str], identity: Any, moment: bool = True) -> str:
    parts: list = [service, site, actor]
    if moment:
        parts += [world.round, world.time if world.continuous else None, world.stage]
    text = json.dumps([parts, plain(identity)], sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def consult(world: Any, *, service: str, method: str, site: str, identity: Any, ask: Callable[[Any], Any],
            actor: Optional[str] = None, validate: Optional[Callable[[Any], Any]] = None,
            fallback: Optional[Callable[[], Any]] = None, moment: bool = True, lock: Any = None) -> Any:
    """The host's answer for this call: recorded, replayed, live, or the declared fallback.

    ``ask(adapter)`` performs the live call; ``validate(answer)`` returns the normalised answer
    or raises :class:`HostError`. Without a recorded answer, a live adapter or a ``fallback``
    the run stops with a contract error naming the host it needs. Callers outside the run's
    lock pass it as ``lock``: the host is asked without it, and the tape is written under it.
    """
    key = request_key(world, service, site, actor, identity, moment)
    hosts = hosts_for(world)
    guard = lock if lock is not None else _LOCK
    with guard:
        tape = _tape(world, site)
        found = tape.get(key)
        if found is None and hosts is not None and key in hosts.replay:
            found = tape[key] = copy.deepcopy(hosts.replay[key])
            world.touch()
    if found is not None:
        return copy.deepcopy(found["response"])
    adapter = hosts.adapter(service) if hosts is not None else None
    if adapter is None:
        if fallback is None:
            if hosts is not None and not hosts.live:
                raise FatalRunError(f"this needs the host '{service}' ({method}), and the replay tape has no answer "
                                    "for this call (the run diverged from the recorded one, or the tape is from "
                                    "another run)", site)
            raise FatalRunError(f"this needs the host '{service}' ({method}), but no answer is recorded and no host "
                                f"is bound; load with fg_env.host.load(..., hosts={{'{service}': ...}}), replay a "
                                "tape, or declare a fallback", site)
        answer = _json_safe(fallback())  # the engine's own answer: not validated
    else:
        if not callable(getattr(adapter, method, None)):
            raise FatalRunError(f"the host '{service}' ({type(adapter).__name__}) has no {method}() method", site)
        answer = _live(adapter, service, site, ask, validate)
    entry: Dict[str, Any] = {"service": service, "site": site, "round": world.round, "actor": actor,
                             "response": answer}
    if adapter is None:
        entry["fallback"] = True
    with guard:
        tape = _tape(world, site)
        if key in tape:  # a concurrent identical call recorded first: everyone reads that answer
            return copy.deepcopy(tape[key]["response"])
        tape[key] = entry
        world.touch()
    return copy.deepcopy(answer)


def _live(adapter: Any, service: str, site: str, ask: Callable[[Any], Any],
          validate: Optional[Callable[[Any], Any]]) -> Any:
    """A live host's answer, validated. An answer the engine cannot use (the host raised :class:`HostError`, or the
    answer is outside the protocol) is asked for once more, the request carrying a `correction` that says what was
    wrong; a second unusable answer, or any other failure, stops the run."""
    correction: Optional[str] = None
    while True:
        asked = adapter if correction is None else _Corrected(adapter, correction)
        try:
            answer = ask(asked)
        except HostError as exc:
            if correction is not None:
                raise FatalRunError(f"host '{service}' failed, also when asked again: {exc}", site) from None
            correction = str(exc)
            continue
        except Exception as exc:  # an adapter defect or provider error: surfaced with its type, never swallowed
            raise FatalRunError(f"host '{service}' raised {type(exc).__name__}: {exc}", site) from exc
        try:
            answer = _json_safe(answer)
            return validate(answer) if validate is not None else answer
        except HostError as exc:
            if correction is not None:
                raise FatalRunError(f"host '{service}' answered outside its protocol, also when asked again: {exc}",
                                    site) from None
            correction = f"your answer was outside the protocol: {exc}"


class _Corrected:
    """A host asked again: a request passed to any of its methods carries `correction`."""

    def __init__(self, adapter: Any, correction: str):
        self._adapter = adapter
        self._correction = correction

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._adapter, name)
        if not callable(method):
            return method

        def asked(request: Any, *rest: Any) -> Any:
            if isinstance(request, Mapping):
                request = {**request, "correction": self._correction}
            return method(request, *rest)

        return asked


def discard(world: Any, key: str) -> None:
    """Take an answer off the tape: the turn that asked for it ran out of time while the host answered, so the run
    never used it (call under the run's lock)."""
    tape = world.props.get(TAPE)
    if isinstance(tape, dict) and tape.pop(key, None) is not None:
        world.touch()


def tape_of(source: Any) -> Dict[str, Dict[str, Any]]:
    """The recorded host answers of an environment (``Env``) or a snapshot, for replay."""
    world = getattr(source, "world", None)
    if world is not None:
        raw = world.props.get(TAPE)
    elif isinstance(source, Mapping) and "props" in source:
        from ..snapshot import decode

        raw = decode(source["props"]).get(TAPE)
    else:
        raise TypeError(f"tape_of needs an Env or a snapshot, got {type(source).__name__}")
    return copy.deepcopy(dict(raw or {}))


def _tape(world: Any, site: str) -> Dict[str, Any]:
    tape = world.props.get(TAPE)
    if not isinstance(tape, dict):
        raise FatalRunError(f"the world property '{TAPE}' is missing; host mechanisms declare it", site)
    return tape


def _json_safe(value: Any, depth: int = 0) -> Any:
    """A JSON copy of a host answer; raises :class:`HostError` for anything else."""
    def check(item: Any, level: int) -> Any:
        if level > _MAX_DEPTH:
            raise HostError("the answer is nested too deeply")
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            return str.__str__(item) if isinstance(item, Untrusted) else item
        if isinstance(item, int):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise HostError(f"numbers must be finite, got {item!r}")
            return item
        if isinstance(item, Mapping):
            out = {}
            for k, v in item.items():
                if not isinstance(k, str):
                    raise HostError(f"object keys must be text, got {k!r}")
                out[str.__str__(k)] = check(v, level + 1)
            return out
        if isinstance(item, (list, tuple)):
            return [check(v, level + 1) for v in item]
        raise HostError(f"answers are JSON data; got {type(item).__name__}")

    safe = check(value, depth)
    if len(json.dumps(safe, ensure_ascii=False)) > MAX_RESPONSE_CHARS:
        raise HostError(f"the answer is longer than {MAX_RESPONSE_CHARS:,} characters")
    return safe
