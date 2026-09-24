"""Where :func:`fg_env.author` evaluates the model's contract: in a child process with a hard time limit.

A contract can make the engine slow without bound — a view over every entity, read by every agent, grows with the
square of their number — and no deadline inside the engine can interrupt one long turn. Checking, testing, running and
previewing a saved contract run in a child Python process that is killed when a call's time is up (and started again
for the next call), so the author's session always goes on and says which step was too slow::

    with Sandbox() as box:
        text = box.call("fg_env.authoring.author:_tool", {"name": "check", "path": "contract.json", "args": {}}, seconds=60)

The function runs in the child with ``kwargs`` and returns plain JSON data; :func:`step` names what it is doing, which
is what :class:`TooSlow` reports.
"""
from __future__ import annotations

import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import IO, Any, Dict, Mapping, Optional

__all__ = ["Sandbox", "step", "TooSlow", "GRACE_SECONDS", "START_SECONDS"]

#: What a call may take beyond its budget: a run passing the safe point its own time budget stops it at.
GRACE_SECONDS = 10.0
#: Longest a new child may take to start Python and import the SDK (not counted in any call's budget).
START_SECONDS = 120.0
#: What starts each line the child writes for the parent; any other output (a print in a model's code) is not read.
_MARK = "\x1efg-author "
#: Whether this process is the child (:func:`step` then reports to the parent).
_IN_CHILD = False


class TooSlow(Exception):
    """A call was still working when its time was up; ``step`` is what it was doing ("" before its first step)."""

    def __init__(self, step: str, seconds: float):
        super().__init__(f"{step or 'starting'} was still going after {seconds:g}s")
        self.step, self.seconds = step, seconds


class Sandbox:
    """One child process, started on the first :meth:`call` and again after one is killed; :meth:`close` ends it."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._ready = False

    def call(self, function: str, kwargs: Mapping[str, Any], seconds: float) -> Any:
        """``function`` (``"module:name"``) called with ``kwargs`` in the child. Raises :class:`TooSlow` when it takes
        longer than ``seconds`` (plus :data:`GRACE_SECONDS`; a new child's start is not counted), and RuntimeError when
        the child dies or it raises."""
        process = self._process or self.start()
        assert process.stdin is not None
        if not self._ready:  # a new child: its start does not count against the call's time
            try:
                self._ready = self._next(process, time.monotonic() + START_SECONDS, "", 0) == {"ready": True}
            except TooSlow:
                raise RuntimeError(f"the test process did not start within {START_SECONDS:g}s") from None
        try:
            process.stdin.write(json.dumps({"function": function, "kwargs": dict(kwargs)}) + "\n")
            process.stdin.flush()
        except OSError:
            pass  # the child is gone: reading says how it ended
        deadline, current = time.monotonic() + seconds + GRACE_SECONDS, ""
        while True:
            message = self._next(process, deadline, current, seconds)
            if "step" in message:
                current = message["step"]
            elif "error" in message:
                raise RuntimeError(message["error"])
            else:
                return message["value"]

    def _next(self, process: subprocess.Popen, deadline: float, current: str, seconds: float) -> Dict[str, Any]:
        """The child's next message; raises :class:`TooSlow` at ``deadline``, RuntimeError when the child ends."""
        try:
            line = self._lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            self.close()
            raise TooSlow(current, seconds) from None
        if line is None:
            code = process.wait()
            self._process = None
            raise RuntimeError(f"the test process died (exit code {code}): the contract broke the engine")
        message: Dict[str, Any] = json.loads(line)
        return message

    def close(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.wait()
            self._process = None

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> subprocess.Popen:
        """Start the child now (it takes a moment to import the SDK); :meth:`call` starts it when needed."""
        # A new queue: nothing a killed child wrote reaches the next call.
        self._lines, self._ready = queue.Queue(), False
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}  # the same fg_env and packages
        process = subprocess.Popen([sys.executable, "-c", "from fg_env.authoring.sandbox import main; main()"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, env=env)
        threading.Thread(target=_read, args=(process.stdout, self._lines), daemon=True).start()
        self._process = process
        return process


def step(what: str) -> None:
    """Say what the child is doing now: what :class:`TooSlow` names if it is still at it when time is up."""
    if _IN_CHILD:
        _say({"step": what})


def main() -> None:
    """The child: answer each request line on stdin with ``{"value": ...}`` or ``{"error": ...}``."""
    global _IN_CHILD
    _IN_CHILD = True
    _say({"ready": True})  # the SDK is imported (this module's package)
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        request: Dict[str, Any] = json.loads(line)
        module, _, name = request["function"].partition(":")
        try:
            answer: Dict[str, Any] = {"value": getattr(importlib.import_module(module), name)(**request["kwargs"])}
        except Exception as exc:  # the caller reads it as the problem
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        _say(answer)


def _say(message: Dict[str, Any]) -> None:
    out = sys.__stdout__  # the pipe to the parent, even if code in the child redirects sys.stdout
    assert out is not None
    out.write(_MARK + json.dumps(message, default=str) + "\n")
    out.flush()


def _read(stream: IO[str], lines: "queue.Queue[Optional[str]]") -> None:
    """Pass the child's lines for the parent to ``lines``, then None when it ends."""
    try:
        for line in stream:
            if line.startswith(_MARK):
                lines.put(line[len(_MARK):])
    finally:
        lines.put(None)
