"""The run's gate: the one lock every change to a run is made holding, and the signal waiting on it wakes on.

A run plays its participants' turns on worker threads and event loops, but only one thing changes the run at a time:
whatever holds the gate (``with gate:``). The engine waits for turns to land by :meth:`Gate.wait`, which lets go of the
gate until a turn lands or a call returns (:meth:`Gate.notify`), so a waiting engine never blocks a turn's calls. The
gate is re-entrant: work holding it may call work that takes it again.
"""
from __future__ import annotations

import threading
from typing import Any

__all__ = ["Gate"]


class Gate:
    """The run's one lock and its signal (see the module docstring)."""

    __slots__ = ("_lock", "_signal")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._signal = threading.Condition(self._lock)

    def __enter__(self) -> Gate:
        self._lock.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._lock.release()

    def wait(self, timeout: float | None = None) -> None:
        """Let go of the gate until :meth:`notify` is called or ``timeout`` seconds pass, then hold it again. Call it
        holding the gate."""
        self._signal.wait(timeout)

    def notify(self) -> None:
        """Wake everything waiting on the gate: a turn landed, a call returned or something held was given back."""
        with self._signal:
            self._signal.notify_all()
