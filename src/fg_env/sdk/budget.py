"""Run budgets: one run's cap on model tokens, tool calls, host calls and wall-clock seconds.

``env.run(participants, budget={"tokens": 200_000, "calls": 500, "host_calls": 50, "seconds": 600,
"on_exhaust": "end"})``. The counts are the ones the run already keeps: ``tokens`` are the input and
output tokens participants report (the built-in LLM participants, or ``wake.record_usage``), ``calls``
the agents' tool calls, ``host_calls`` the host answers on the run's tape (live or replayed; a declared
fallback costs nothing), ``seconds`` the wall-clock time spent inside ``run``.

A budget is checked at the run's safe points — before every round, stage, pass and sequential turn —
so a turn in progress always finishes and, for coded participants, the run stops at the same point on
every replay (``seconds`` is wall-clock time, so it is the one limit that is not deterministic). Once a
limit is reached, ``on_exhaust: "end"`` ends the run there (``ended_by: "budget"``, outputs computed as
for any ended run) and ``"idle"`` keeps the world running while every agent's later turns are idle.
``result.budget`` reports the limits, what was used and which limit ran out; snapshots carry it.
"""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["Budget", "LIMITS", "ON_EXHAUST"]

#: Every limit a budget may set, in the order they are checked.
LIMITS = ("tokens", "calls", "host_calls", "seconds")
ON_EXHAUST = ("end", "idle")
_WHAT = {"tokens": "token", "calls": "tool call", "host_calls": "host call", "seconds": "time"}


class Budget:
    """A run's limits, and which one ran out (set at a safe point, never undone)."""

    def __init__(self, limits: Mapping[str, float], on_exhaust: str = "end", exhausted: Optional[str] = None,
                 seconds: float = 0.0):
        self.limits = dict(limits)
        self.on_exhaust = on_exhaust
        self.exhausted = exhausted
        #: Wall-clock seconds spent in earlier ``run`` calls (and before a snapshot).
        self.seconds = seconds
        self._started: Optional[float] = None

    @classmethod
    def parse(cls, value: Any) -> "Budget":
        """A budget from ``{"tokens": N, "calls": N, "host_calls": N, "seconds": S, "on_exhaust": ...}``."""
        if not isinstance(value, Mapping):
            raise ValueError(f"budget must be a mapping like {{'tokens': 100000, 'on_exhaust': 'end'}}, got {value!r}")
        unknown = [key for key in value if key not in LIMITS and key != "on_exhaust"]
        if unknown:
            raise ValueError(f"budget has no {', '.join(map(repr, unknown))} (limits: {', '.join(LIMITS)}; "
                             "and on_exhaust)")
        limits: Dict[str, float] = {}
        for key in LIMITS:
            if key not in value:
                continue
            limit = value[key]
            whole = key != "seconds"
            if isinstance(limit, bool) or not isinstance(limit, int if whole else (int, float)) \
                    or not math.isfinite(limit) or limit <= 0:
                kind = "a whole number" if whole else "a number of seconds"
                raise ValueError(f"budget {key} must be {kind} > 0, got {limit!r}")
            limits[key] = limit
        if not limits:
            raise ValueError(f"budget sets no limit; give at least one of {', '.join(LIMITS)}")
        on_exhaust = value.get("on_exhaust", "end")
        if on_exhaust not in ON_EXHAUST:
            raise ValueError(f"budget on_exhaust must be {' or '.join(map(repr, ON_EXHAUST))}, got {on_exhaust!r}")
        return cls(limits, on_exhaust)

    @staticmethod
    def begin(given: Optional["Budget"], current: Optional["Budget"]) -> Optional["Budget"]:
        """The budget a ``run`` call plays under, its clock started: ``given`` — which takes over the time
        ``current`` already spent — else ``current``."""
        budget = given if given is not None else current
        if budget is not None:
            if given is not None and current is not None:
                given.seconds = current.elapsed()
            budget.start()
        return budget

    # -- time -----------------------------------------------------------------------

    def start(self) -> None:
        self._started = time.monotonic()

    def pause(self) -> None:
        self.seconds = self.elapsed()
        self._started = None

    def elapsed(self) -> float:
        return self.seconds + (time.monotonic() - self._started if self._started is not None else 0.0)

    # -- use -----------------------------------------------------------------------

    def used(self, env: "Env") -> Dict[str, float]:
        from .host.tape import TAPE

        tape = env.world.props.get(TAPE)
        entries = tape.values() if isinstance(tape, Mapping) else ()
        return {"tokens": env.stats.input_tokens + env.stats.output_tokens, "calls": env.stats.calls,
                "host_calls": sum(1 for entry in entries if isinstance(entry, Mapping) and not entry.get("fallback")),
                "seconds": round(self.elapsed(), 3)}

    def check(self, env: "Env") -> Optional[str]:
        """The limit that has run out (recorded the first time one does), or None."""
        if self.exhausted is None:
            used = self.used(env)
            self.exhausted = next((key for key in LIMITS if key in self.limits and used[key] >= self.limits[key]), None)
        return self.exhausted

    def enforce(self, env: "Env") -> bool:
        """Apply the budget at one of the run's safe points. True when a limit has just run out and the run ended
        here; with ``on_exhaust: "idle"`` a ``budget`` event is logged, the run goes on and the driver idles every
        later turn."""
        if self.exhausted is not None or self.check(env) is None:
            return False
        if self.on_exhaust == "idle":
            with env._lock:
                env.world.emit("budget", self.message(env), data=self.to_dict(env))
                env.world.journal.clear()
            env._flush_events()
            return False
        if env._cursor is not None:
            env._cursor.close()
            env._cursor = None
        env.world.request_end("budget", None, self.message(env))
        env._finish()
        return True

    def message(self, env: "Env") -> str:
        key = self.exhausted
        if key is None:
            return ""
        used, limit = self.used(env)[key], self.limits[key]
        spent = f"{used:g} of {limit:g} seconds" if key == "seconds" else f"{used:,} of {limit:,}"
        then = "the run ended" if self.on_exhaust == "end" else "agents take no more actions"
        return f"The {_WHAT[key]} budget ran out ({spent}); {then}."

    def to_dict(self, env: "Env") -> Dict[str, Any]:
        return {"limits": dict(self.limits), "on_exhaust": self.on_exhaust, "used": self.used(env),
                "exhausted": self.exhausted}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Budget":
        """The budget kept in a snapshot or a result."""
        budget = cls.parse({**data["limits"], "on_exhaust": data.get("on_exhaust", "end")})
        budget.exhausted = data.get("exhausted")
        budget.seconds = float((data.get("used") or {}).get("seconds", 0.0))
        return budget
