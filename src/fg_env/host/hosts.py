"""The host adapters a run may consult, and how a run is bound to them.

Contracts name hosts (``"host": "judge"``); a :class:`Hosts` maps those names to adapter
objects. A run is bound to its hosts for its lifetime without touching the core objects: the
binding is held here, keyed weakly by the run's world.

The model tokens a host call spends join the stats — and so the token budget — of the run that made the call, at the
run's safe points and when its result is read. The reference adapters report each call's tokens as they make it
(:func:`credit_tokens`), so runs in parallel that share one adapter each count exactly their own; an adapter of your
own is counted by how much its ``usage`` counters (``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
``cache_write_tokens``) grew during the call, which is exact unless parallel runs share it.
"""
from __future__ import annotations

import threading
import weakref
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

if TYPE_CHECKING:
    from ..runtime import Env

__all__ = ["Hosts", "HostsLike", "as_hosts", "bind", "hosts_for", "count_host_tokens", "counting", "credit_tokens"]

#: The counters of a host's ``usage`` that are model tokens.
_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class Hosts:
    """Host adapters by name, plus an optional recorded tape to replay.

    ``Hosts({"judge": my_evaluator, "web_search": my_tools})`` consults live adapters.
    ``Hosts.replaying(tape)`` answers only from a tape recorded by an earlier run
    (:func:`fg_env.host.tape_of`), so a replay never reaches a model or the network.
    With both, recorded answers win and anything new goes to the live adapter.
    """

    def __init__(self, adapters: Optional[Mapping[str, Any]] = None, *,
                 replay: Optional[Mapping[str, Mapping[str, Any]]] = None, live: bool = True):
        if adapters is not None and not isinstance(adapters, Mapping):
            raise TypeError(f"adapters must be a mapping of host name to adapter, got {type(adapters).__name__}")
        if replay is not None and not isinstance(replay, Mapping):
            raise TypeError(f"replay must be a tape (a mapping), got {type(replay).__name__}")
        for name, adapter in (adapters or {}).items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"host names are non-empty text, got {name!r}")
            if adapter is None:
                raise ValueError(f"host '{name}' has no adapter")
        for key, entry in (replay or {}).items():
            if not isinstance(key, str) or not isinstance(entry, Mapping) or "response" not in entry:
                raise ValueError("replay must be a tape from fg_env.host.tape_of()")
        self._adapters: Dict[str, Any] = dict(adapters or {})
        self.replay: Dict[str, Dict[str, Any]] = {key: dict(entry) for key, entry in (replay or {}).items()}
        self.live = bool(live)

    @classmethod
    def replaying(cls, tape: Mapping[str, Mapping[str, Any]]) -> "Hosts":
        """Hosts that answer only from a recorded tape."""
        return cls(replay=tape, live=False)

    def adapter(self, name: str) -> Any:
        """The live adapter for ``name``, or None."""
        return self._adapters.get(name) if self.live else None

    @property
    def names(self) -> List[str]:
        return list(self._adapters)

    def __repr__(self) -> str:
        mode = "live" if self.live else "replay only"
        return f"Hosts({', '.join(self._adapters) or 'no adapters'}; {len(self.replay)} recorded; {mode})"


HostsLike = Union[Hosts, Mapping[str, Any], None]

_BOUND: "weakref.WeakKeyDictionary[Any, Hosts]" = weakref.WeakKeyDictionary()


def as_hosts(value: HostsLike) -> Optional[Hosts]:
    if value is None or isinstance(value, Hosts):
        return value
    if isinstance(value, Mapping):
        return Hosts(value)
    raise TypeError(f"hosts must be a Hosts or a mapping of host name to adapter, got {type(value).__name__}")


def bind(env: "Env", hosts: HostsLike) -> "Env":
    """Bind a loaded environment to its hosts (``None`` unbinds). Returns the environment.

    Previews play the next round on a restored copy of the run; the copy is bound to the same
    hosts, so a preview shows the turn exactly as it will be (and may consult a live host).
    """
    resolved = as_hosts(hosts)
    if resolved is None:
        _BOUND.pop(env.world, None)
        return env
    _BOUND[env.world] = resolved
    if not getattr(env, "_host_probe_bound", False):
        make_probe = env.previews.probe

        def probe(snapshot: Mapping[str, Any], participants: Any = None) -> "Env":
            copy = make_probe(snapshot, participants)
            current = _BOUND.get(env.world)
            return bind(copy, current) if current is not None else copy

        env.previews.probe = probe  # type: ignore[method-assign]
        env._host_probe_bound = True  # type: ignore[attr-defined]
    return env


def hosts_for(world: Any) -> Optional[Hosts]:
    return _BOUND.get(world)


def count_host_tokens(env: "Env") -> None:
    """Add the tokens the run's host calls spent since last counted to the run's stats."""
    with _PENDING_LOCK:
        taken = _PENDING.pop(env.world, None)
    if taken and any(taken):
        from ..measure import Stats

        with env._lock:
            env.stats.add(Stats(**dict(zip(_TOKENS, taken))))


@contextmanager
def counting(world: Any, adapter: Any) -> Iterator[None]:
    """Count the model tokens a host call made inside the block spends toward the run of ``world``: what the adapter
    reports with :func:`credit_tokens` while the block runs on this thread, else how much its ``usage`` grew."""
    before = _tokens(adapter)
    outer = getattr(_CALL, "reported", None)
    reported: Dict[str, int] = {}
    _CALL.reported = reported
    try:
        yield
    finally:
        _CALL.reported = outer
        if reported:
            spent = [reported.get(key, 0) for key in _TOKENS]
        else:
            spent = [max(0, after - start) for after, start in zip(_tokens(adapter), before)]
        if any(spent):
            with _PENDING_LOCK:
                pending = _PENDING.setdefault(world, [0] * len(_TOKENS))
                pending[:] = [total + more for total, more in zip(pending, spent)]


def credit_tokens(input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0,
                  cache_write_tokens: int = 0) -> None:
    """Report the tokens one model call of a host adapter spent, toward the run whose host call is in progress on this
    thread (the reference adapters call it; outside a host call it does nothing). ``input_tokens`` are the fresh ones,
    not read from the provider's prompt cache."""
    reported = getattr(_CALL, "reported", None)
    if reported is not None:
        spent = zip(_TOKENS, (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens))
        for key, value in spent:
            reported[key] = reported.get(key, 0) + value


#: The host call in progress on this thread, and the tokens each run's host calls spent since it last counted them.
_CALL = threading.local()
_PENDING: "weakref.WeakKeyDictionary[Any, List[int]]" = weakref.WeakKeyDictionary()
_PENDING_LOCK = threading.Lock()


def _tokens(adapter: Any) -> Tuple[int, ...]:
    usage = getattr(adapter, "usage", None)
    counts = [usage.get(key, 0) if isinstance(usage, Mapping) else 0 for key in _TOKENS]
    return tuple(value if isinstance(value, int) and not isinstance(value, bool) else 0 for value in counts)
