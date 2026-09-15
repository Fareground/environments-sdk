"""Loading, restoring and running an environment with its hosts.

These wrap the core entry points until they take ``hosts=`` themselves (see the core hook
spec): :func:`load` binds the hosts and writes build-time host work (personas) before round 1,
:func:`restore` binds a restored run, :func:`run` offers in-turn host tools to participants.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .hosts import HostsLike, bind

if TYPE_CHECKING:
    from ..measure import RunResult
    from ..runtime import Env

__all__ = ["load", "build", "restore", "run", "wrap"]


def load(source: Any, *, hosts: HostsLike = None, **kwargs: Any) -> "Env":
    """``fg_env.load`` bound to ``hosts``, with build-time host work done (personas).

    ``hosts`` is a :class:`~fg_env.sdk.host.Hosts` or a mapping of host name to adapter; other
    keyword arguments go to ``fg_env.load``.
    """
    from ..api import load as load_env

    env = load_env(source, **kwargs)
    bind(env, hosts)
    build(env)
    return env


def build(env: "Env") -> None:
    """Write everything hosts contribute when the world is built (personas). Idempotent."""
    from .personas import generate

    names = [name for name, raw in env.contract.mechanisms.items()
             if isinstance(raw, Mapping) and raw.get("kind") == "personas"]
    if not names or env.world.round:
        return
    with env._lock:
        for name in names:
            generate(env.world, name, f"mechanisms.{name}")
        env._check_invariants("personas")
        env.world.journal.clear()


def restore(contract: Any, snapshot: Mapping[str, Any], *, hosts: HostsLike = None, parallel: int = 8) -> "Env":
    """``Env.restore`` bound to ``hosts``. A restored run never asks again for recorded answers."""
    from ..runtime import Env

    return bind(Env.restore(contract, snapshot, parallel=parallel), hosts)


def run(env: "Env", participants: Any = None, **kwargs: Any) -> "RunResult":
    """``env.run`` with the contract's in-turn host tools offered to every participant."""
    return env.run(wrap(env, participants), **kwargs)


def wrap(env: "Env", participants: Any = None) -> Any:
    """Participants whose wakes offer the contract's in-turn host tools (for ``env.run``)."""
    from .turn_tools import wrap as wrap_participants

    return wrap_participants(env, participants)
