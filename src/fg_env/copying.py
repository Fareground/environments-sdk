"""Copying a run: the part of :class:`~fg_env.runtime.Env` that clones and forks it."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["Copying"]


class Copying:
    """Clones and forks of a run (see :mod:`fg_env.branch` and :mod:`fg_env.forks`)."""

    def clone(self: "Env") -> "Env":  # type: ignore[misc]
        """An independent copy of this run now, continuing exactly as it would.

        Between rounds it is a restored snapshot; a run stopped part-way through a round (``run(stop=...)``) is
        copied by replaying it, so the copy stops at the same point. Inside a turn, use ``wake.clone()``.
        """
        from .branch import clone_env

        return clone_env(self)

    def fork(self: "Env", **changes: Any) -> "Env":  # type: ignore[misc]
        """A new run continuing this one from now under changes, leaving this run untouched: another ``arm``
        (``None`` for none), ``inputs``, a contract ``patch`` or a whole replacement ``contract``, a ``seed`` for
        the luck from here on, and intervention ``effects`` applied at the fork (logged as a `fork` event,
        invariants checked). Without changes it is :meth:`clone`.

        Changes apply between rounds. Whatever the changed contract cannot hold of the current state is refused
        with a :class:`~fg_env.ContractError` listing each problem and its fix. See :func:`fg_env.fork`.
        """
        from .forks import fork_env

        return fork_env(self, **changes)
