"""Run budgets: one run's cap on model tokens, tool calls, host calls and wall-clock seconds.

``env.run(participants, budget={"tokens": 200_000, "calls": 500, "host_calls": 50, "seconds": 600,
"on_exhaust": "end"})``. The counts are the ones the run already keeps: ``tokens`` are the model tokens
participants and hosts report (the built-in LLM participants, or ``wake.record_usage``) — input, output and cache
writes in full, cache reads at :data:`CACHED_WEIGHT` of one, as providers bill them — ``calls``
the agents' tool calls, ``host_calls`` the host answers on the run's tape (live or replayed; a declared
fallback costs nothing), ``seconds`` the wall-clock time spent inside ``run``. A host call's tokens count as it returns,
and a live host call is not made once the token or host-call limit is reached (a judge leaves that text unscored, a
game master refuses that attempt); the run's end checks the budget once more, so a run that overspent says so.

A budget is checked at the run's safe points — before every round, stage, pass and sequential turn — so, for coded
participants, the run stops at the same point on every replay (``seconds`` is wall-clock time, so it is the one limit
that is not deterministic). ``tokens`` is also checked each time a participant reports usage, counting the turns still
in play: once it is reached, every turn in play ends there (calls made after that are refused). The built-in LLM
participants also hold back a model call while the calls already under way may spend what is left (each reserves what
the participant's previous call used; its first call, its prompt and the most it may write — its ``max_tokens``, or
all that is left when none is set, so that one call runs alone), so parallel turns overshoot the limit by at most
about one call, not one call per turn in flight, and a budget far from its limit never makes their later calls wait
for each other. Once a limit is reached, ``on_exhaust: "end"`` ends the run there (``ended_by: "budget"``, outputs
computed as for any ended run) and ``"idle"`` keeps the world running while every agent's later turns are idle.
``result.budget`` reports the limits, what was used and which limit ran out; snapshots carry it.

Usage a participant reports after its turn is over (it ran out of time) still counts: it is added to the run's
statistics when it arrives and the next safe point sees it, though that participant takes no further action
(when it arrives depends on the clock, as ``seconds`` does). Batches — ``experiment`` (with ``branch_at`` the
shared rounds count toward every arm), ``tournament``, ``evaluate`` and ``run_jobs`` — give every run the whole
budget to itself.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ..contract.base import TAPE
from ..host.hosts import count_host_tokens

if TYPE_CHECKING:
    from .env import Env
    from .turn import Turn

__all__ = ["Budget", "LIMITS", "ON_EXHAUST", "CACHED_WEIGHT", "is_seconds", "tokens_of"]

#: Every limit a budget may set, in the order they are checked.
LIMITS = ("tokens", "calls", "host_calls", "seconds")
ON_EXHAUST = ("end", "idle")
_WHAT = {"tokens": "token", "calls": "tool call", "host_calls": "host call", "seconds": "time"}
#: What an input token read from the provider's prompt cache counts for in a token budget: providers bill it at a small
#: fraction of a fresh one (a tenth on Anthropic, a tenth to a half on OpenAI-compatible servers). A cache write counts
#: in full.
CACHED_WEIGHT = 0.1


def tokens_of(stats: Any) -> int:
    """The tokens ``stats`` (a :class:`~fg_env.runtime.facts.Stats`) spend toward a token budget: fresh input, output
    and cache writes in full, cache reads at :data:`CACHED_WEIGHT`."""
    return math.ceil(stats.input_tokens + stats.output_tokens + stats.cache_write_tokens
                     + stats.cache_read_tokens * CACHED_WEIGHT)


def is_seconds(value: Any) -> bool:
    """Whether ``value`` is a usable span of time: a finite number of seconds above zero."""
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


class Budget:
    """A run's limits, and which one ran out (set at a safe point, never undone)."""

    def __init__(self, limits: Mapping[str, float], on_exhaust: str = "end"):
        self.limits = dict(limits)
        self.on_exhaust = on_exhaust
        self.exhausted: str | None = None
        #: Wall-clock seconds spent running, up to the last safe point.
        self.seconds = 0.0
        self._mark: float | None = None
        #: Tokens held for the model calls under way (:meth:`reserve`).
        self._reserved = 0.0

    @classmethod
    def parse(cls, value: Any) -> Budget:
        """A budget from ``{"tokens": N, "calls": N, "host_calls": N, "seconds": S, "on_exhaust": ...}``."""
        if not isinstance(value, Mapping):
            raise ValueError(f"budget must be a mapping like {{'tokens': 100000, 'on_exhaust': 'end'}}, got {value!r}")
        unknown = [key for key in value if key not in LIMITS and key != "on_exhaust"]
        if unknown:
            raise ValueError(f"budget has no {', '.join(map(repr, unknown))} (limits: {', '.join(LIMITS)}; "
                             "and on_exhaust)")
        limits: dict[str, float] = {}
        for key in LIMITS:
            if key not in value:
                continue
            limit = value[key]
            if key == "seconds" and not is_seconds(limit):
                raise ValueError(f"budget seconds must be a number of seconds > 0, got {limit!r}")
            if key != "seconds" and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
                raise ValueError(f"budget {key} must be a whole number > 0, got {limit!r}")
            limits[key] = limit
        if not limits:
            raise ValueError(f"budget sets no limit; give at least one of {', '.join(LIMITS)}")
        on_exhaust = value.get("on_exhaust", "end")
        if on_exhaust not in ON_EXHAUST:
            raise ValueError(f"budget on_exhaust must be {' or '.join(map(repr, ON_EXHAUST))}, got {on_exhaust!r}")
        return cls(limits, on_exhaust)

    @classmethod
    def begin(cls, given: Any, current: Budget | None) -> Budget | None:
        """The budget a ``run`` call plays under: ``given`` (see :meth:`parse`) — keeping the seconds ``current``
        already counted — else ``current``. Its clock starts now."""
        budget = current if given is None else cls.parse(given)
        if budget is not None:
            if given is not None and current is not None:
                budget.seconds = current.seconds
            budget._mark = time.monotonic()
        return budget

    def copy(self) -> Budget:
        """The same limits and what they have counted, for a copy of the run (its clock starts when it runs)."""
        budget = Budget(self.limits, self.on_exhaust)
        budget.exhausted, budget.seconds = self.exhausted, self.seconds
        return budget

    @staticmethod
    def report(env: Env) -> dict[str, Any]:
        """The run's budget as ``result.budget`` shows it (empty without one)."""
        return env.budget.to_dict(env) if env.budget is not None else {}

    def used(self, env: Env) -> dict[str, float]:
        tape = env.world.props.get(TAPE)
        entries = tape.values() if isinstance(tape, Mapping) else ()
        return {"tokens": tokens_of(env.state.stats), "calls": env.state.stats.calls,
                "host_calls": sum(1 for entry in entries if isinstance(entry, Mapping) and not entry.get("fallback")),
                "seconds": round(self.seconds, 3)}

    def tokens_spent(self, env: Env, turn: Turn) -> bool:
        """Whether the token limit is reached counting the turns still in play (``turn``, and in a simultaneous stage
        all of its turns), whose usage joins the run's totals only when they finish (call under the run's gate)."""
        return self.tokens_left(env, turn) <= 0

    def tokens_left(self, env: Env, turn: Turn) -> float:
        """The tokens left before the limit, counting the turns still in play (``turn``, and in a simultaneous stage all
        of its turns), whose usage joins the run's totals only when they finish (call under the run's gate)."""
        limit = self.limits.get("tokens")
        if limit is None:
            return math.inf
        playing = {id(t): t for t in (*env.state.staged, turn) if not t.tallied}
        return limit - tokens_of(env.state.stats) - sum(tokens_of(t.stats) for t in playing.values())

    def reserve(self, env: Env, turn: Turn, expected: Callable[[], float]) -> float | None:
        """Hold what a model call ``turn`` is about to make is ``expected`` to spend of the token limit (asked again
        each time the call is woken, since calls that finish meanwhile may tell more; ``math.inf``: unknown, so the
        call runs alone), waiting while the calls already under way may spend what is left. Returns what was held, to
        :meth:`release` once the call's usage is recorded, or None when the turn is over first (the limit ran out, or
        its time did). A call waits only for others, so a limit is overshot by about one call."""
        if "tokens" not in self.limits:
            return 0
        gate = env.gate
        with gate:
            while not turn.done:
                left = self.tokens_left(env, turn)
                if left <= 0:
                    return None
                tokens = expected()
                if not self._reserved or tokens <= left - self._reserved:
                    held = min(tokens, left)
                    self._reserved += held
                    return held
                time_left = turn.time_left()
                if time_left is not None and time_left <= 0:
                    return None
                gate.wait(time_left)
            return None

    def release(self, env: Env, held: float) -> None:
        """Give back what :meth:`reserve` held for a call that has finished."""
        if held:
            with env.gate:
                self._reserved -= held
                env.gate.notify()

    def host_refusal(self, env: Env) -> str | None:
        """Why a live host call may not be made now — the token limit (counting what host calls spent so far) or the
        host-call limit is reached — or None."""
        count_host_tokens(env)
        used = self.used(env)
        key = next((key for key in ("tokens", "host_calls") if key in self.limits and used[key] >= self.limits[key]),
                   None)
        return None if key is None else f"the run's {_WHAT[key]} budget ran out " \
                                        f"({spent(key, used[key], self.limits[key])})"

    def check(self, env: Env) -> str | None:
        """The limit that has run out (recorded the first time one does), or None. Called at safe points: the
        wall-clock time since the previous one is counted."""
        if self.exhausted is None:
            now = time.monotonic()
            if self._mark is not None:
                self.seconds += now - self._mark
            self._mark = now
            count_host_tokens(env)
            used = self.used(env)
            self.exhausted = next((key for key in LIMITS if key in self.limits and used[key] >= self.limits[key]), None)
        return self.exhausted

    def enforce(self, env: Env) -> bool:
        """Apply the budget at one of the run's safe points. True when a limit has just run out and the run ended
        here; with ``on_exhaust: "idle"`` a ``budget`` event is logged, the run goes on and the driver idles every
        later turn."""
        if self.exhausted is not None or self.check(env) is None:
            return False
        if self.on_exhaust == "idle":
            with env.gate:
                env.world.emit("budget", self.message(env), data=self.to_dict(env))
                env.world.commit()
            env.schedule.flush()
            return False
        env.schedule.abandon()
        env.world.request_end("budget", None, self.message(env))
        env.schedule.finish()
        return True

    def message(self, env: Env) -> str:
        key = self.exhausted
        if key is None:
            return ""
        then = "the run ended" if self.on_exhaust == "end" else "agents take no more actions"
        return f"The {_WHAT[key]} budget ran out ({spent(key, self.used(env)[key], self.limits[key])}); {then}."

    def to_dict(self, env: Env) -> dict[str, Any]:
        return {"limits": dict(self.limits), "on_exhaust": self.on_exhaust, "used": self.used(env),
                "exhausted": self.exhausted}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Budget:
        """The budget kept in a snapshot."""
        budget = cls.parse({**data["limits"], "on_exhaust": data.get("on_exhaust", "end")})
        budget.exhausted = data.get("exhausted")
        budget.seconds = float((data.get("used") or {}).get("seconds", 0.0))
        return budget


def spent(key: str, used: float, limit: float) -> str:
    """How much of one limit was used, as a person reads it."""
    return f"{used:g} of {limit:g} seconds" if key == "seconds" else f"{used:,} of {limit:,}"
