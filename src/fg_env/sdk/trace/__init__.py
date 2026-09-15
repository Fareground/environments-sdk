"""Traces: read a recorded run turn by turn, and replay it offline.

A trace is a run result that recorded exposures (``fg_env.run(..., exposures=True)``, or
``fg-env run --trace run.jsonl``): what every agent read, the tools it was offered, every call with its
result, plus the host answers.

* :func:`trace` — ``overview()``, ``turn()``, ``timeline()``, ``search()``, ``invalid()``, ``agent()``;
  each gives a :class:`TraceView` (``.data`` for code, ``str()`` for people).
* :meth:`Trace.replay` — run the contract again with the recorded calls and host answers, and report
  the first place the run no longer matches its recording.
* :class:`Replayer` (``fg_env.participants.replay``) — the replaying participant on its own.
"""
from .reader import Trace, TraceView, trace
from .replay import ReplayDivergence, Replayer, ReplayResult

__all__ = ["trace", "Trace", "TraceView", "Replayer", "ReplayResult", "ReplayDivergence"]
