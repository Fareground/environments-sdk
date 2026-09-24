"""External data feeds: live or historical values a host writes into world props or records.

At the start of a due round — after scheduled effects, before events and physics — each feed asks
its host adapter (``fetch(request)``) for data and writes the answer into ``world.<prop>`` or
``records.<record>``, atomically. Answers go through the host tape (:func:`host.tape.consult`), so
a snapshot, a restore or a replay reads the recorded answer and never asks again. Text a host
supplies is marked untrusted before any agent reads it. Without a bound host a declared
``fallback`` answers instead; its random draws come from a stream derived from the run seed, the
feed and the round, so replaying a recorded fallback never shifts any other draw.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from ..contract import FeedSpec
from ..contract.base import TAPE
from ..errors import RunError
from ..expr import ExprError, Untrusted, compile_expr, resolve, truthy
from ..host.protocols import HostError
from ..host.tape import consult, plain, request_key
from ..world.live import Abort
from ..world.props import prop_type

if TYPE_CHECKING:
    from ..world.live import SdkWorld
    from .env import Env

__all__ = ["run_feeds", "feed_target"]


def feed_target(spec: FeedSpec) -> tuple[str, str]:
    """``("world", prop)`` or ``("records", record)`` from a feed's ``into``."""
    owner, _, name = spec.into.partition(".")
    return owner, name


def run_feeds(env: Env) -> None:
    """Write this round's due feeds into the world, each in its own atomic change."""
    world = env.world
    for name, spec in env.contract.feeds.items():
        if env._ended():
            return
        if not _due(world, name, spec):
            continue
        with env._lock:
            mark = world.journal.mark()
            try:
                _pull(world, name, spec)
            except BaseException:
                world.journal.rollback(mark)
                raise
            env._after_commit(f"mechanisms.{name}")


def _due(world: SdkWorld, name: str, spec: FeedSpec) -> bool:
    if (world.round - 1) % spec.every != 0:
        return False
    if spec.when is None:
        return True
    try:
        return truthy(compile_expr(spec.when)(world.scope()))
    except ExprError as exc:
        raise RunError(str(exc), f"mechanisms.{name}.when") from None


def _pull(world: SdkWorld, name: str, spec: FeedSpec) -> None:
    path = f"mechanisms.{name}"
    owner, target = feed_target(spec)
    try:
        query = plain(resolve(copy.deepcopy(spec.query), world.scope()))
    except ExprError as exc:
        raise RunError(str(exc), f"{path}.query") from None
    request = {"feed": name, "query": query, "into": spec.into, "expects": _expects(world, owner, target),
               "round": world.round, "date": world.date()}
    identity = {"query": query}
    fallback = partial(_fallback, world, name, spec) if "fallback" in spec.model_fields_set else None
    answer = consult(world, service=spec.host, method="fetch", site=path, identity=identity,
                     ask=partial(_ask, request), validate=partial(_validate, world, owner, target), fallback=fallback)
    recorded = world.props[TAPE].get(request_key(world, spec.host, path, None, identity), {})
    if not recorded.get("fallback"):  # the contract's own fallback is trusted; a host's answer is not
        answer = _untrusted(answer)
    _write(world, owner, target, answer, path)


def _ask(request: dict[str, Any], adapter: Any) -> Any:
    return adapter.fetch(request)


def _expects(world: SdkWorld, owner: str, target: str) -> Any:
    if owner == "world":
        spec = world.contract.world[target]
        return {"type": prop_type(spec), **({"values": spec.values} if spec.values else {}),
                **({"min": spec.min} if spec.min is not None else {}),
                **({"max": spec.max} if spec.max is not None else {})}
    return {"entries": dict(world.contract.records[target].fields)}


def _fallback(world: SdkWorld, name: str, spec: FeedSpec) -> Any:
    with world.drawing_from(world.seeds.rng("feeds", name, world.round)):
        try:
            return plain(resolve(copy.deepcopy(spec.fallback), world.scope()))
        except ExprError as exc:
            raise RunError(str(exc), f"mechanisms.{name}.fallback") from None


def _validate(world: SdkWorld, owner: str, target: str, answer: Any) -> Any:
    """A host's answer in the shape its target takes, or :class:`HostError`."""
    if owner == "world":
        try:
            return world._coerce(world.contract.world[target], answer, f"world.{target}")
        except RunError as exc:
            raise HostError(str(exc)) from None
        except Abort as refusal:  # an answer outside the property's bounds is as unusable as one of the wrong type
            raise HostError(refusal.reason) from None
    fields = world.contract.records[target].fields
    entries = _entries(answer)
    if entries is None:
        raise HostError(f"a record answer is one entry (an object of fields) or a list of them, got {answer!r:.80}")
    for entry in entries:
        unknown = sorted(set(entry) - set(fields))
        if unknown:
            raise HostError(f"record '{target}' has no fields {unknown} (fields: {', '.join(fields)})")
    return entries


def _entries(answer: Any) -> Any:
    if answer is None:
        return []
    if isinstance(answer, Mapping):
        return [dict(answer)]
    if isinstance(answer, list) and all(isinstance(entry, Mapping) for entry in answer):
        return [dict(entry) for entry in answer]
    return None


def _write(world: SdkWorld, owner: str, target: str, answer: Any, path: str) -> None:
    if owner == "world":
        world.set_world(target, answer)
        return
    entries: list[dict[str, Any]] = _entries(answer)
    if entries is None:
        raise RunError(f"a record feed gives one entry (an object of fields) or a list of them, got {answer!r:.80}",
                       path)
    for entry in entries:
        world.post(target, entry, None, None, path)


def _untrusted(value: Any) -> Any:
    """Every text in a host's answer — keys included — marked as participant-grade text."""
    if isinstance(value, str):
        return Untrusted(value)
    if isinstance(value, list):
        return [_untrusted(item) for item in value]
    if isinstance(value, dict):
        return {_untrusted(key): _untrusted(item) for key, item in value.items()}
    return value
