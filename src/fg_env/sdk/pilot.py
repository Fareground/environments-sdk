"""Piloting a copy of a run from outside: it pauses wherever a controlled agent must decide.

A :class:`Pilot` runs an environment on its own thread. Turns of the agents it controls, and
chance nodes when chance is explicit, suspend that thread; the controller (search code, a gym, a
lookahead) reads the paused world and sends tool calls or chance outcomes, which are executed on
the run's thread, inside the turn, exactly as a participant's would be. Everyone else is played by
participants. A copy first plays back the tape of the run it was taken from (:mod:`.replay`), then
pauses at the same decision. Turn time limits never run out in a copy (a paused search has no
clock); turns that timed out in the original replay their timeouts.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import AbstractSet, Any, Callable, List, Optional, Tuple

from ..entity import Entity
from .chance import ChanceNode, sample
from .driving import Driver
from .errors import RunError
from .expr import ExprError
from .participants import Participant
from .replay import Playback
from .runtime import Env
from .session import ToolResult, Wake

__all__ = ["PilotedEnv", "Pilot", "Pause"]


class _Aborted(BaseException):
    """The copy was closed while its run waited for a decision."""


@dataclass
class Pause:
    """What a paused run is waiting for: a turn's next call, or a chance node's outcome."""

    kind: str
    wake: Optional[Wake] = None
    node: Optional[ChanceNode] = None


class PilotedEnv(Env):
    """A run whose turns go through a :class:`Pilot` (playback, then the controller or participants)."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.driver: Driver = _SeatDriver(self)
        self.pilot: Optional[Pilot] = None
        #: The seed the world was built with (a copy may later draw its luck from another seed).
        self.build_seed = self.seed

    def wake_for(self, wake: Wake) -> Wake:
        """The wake a participant gets for this turn: offering the contract's in-turn host tools if any."""
        tools = self.driver.turn_tool_specs()
        if not tools:
            return wake
        from .host.turn_tools import HostWake

        return HostWake(wake._turn, tools)


class _SeatDriver(Driver):
    """Hands every turn to the pilot, which plays back, pauses or passes it on to the bound participant."""

    #: A copy has no wall clock: a turn paused for search never runs out of time.
    timed = False

    def participant(self, actor: Entity) -> Participant:
        inner = super().participant(actor)
        pilot = getattr(self.env, "pilot", None)
        return inner if pilot is None else _Seat(pilot, inner)

    def bound(self, actor: Entity) -> Participant:
        """The participant bound to ``actor`` now, without the pilot."""
        return super().participant(actor)


class _Seat:
    concurrent = False

    def __init__(self, pilot: "Pilot", inner: Participant):
        self.pilot = pilot
        self.inner = inner

    def __call__(self, wake: Wake) -> Any:
        return self.pilot.take_turn(wake, self.inner)


class Pilot:
    """Runs ``env`` with ``playback`` first, pausing for turns of ``controlled`` agents and, when
    ``explicit``, for chance nodes."""

    def __init__(self, env: PilotedEnv, *, playback: Optional[Playback] = None, controlled: AbstractSet[str] = frozenset(),
                 explicit: bool = False, checkpoints: bool = False):
        self.env = env
        self.playback = playback
        self.controlled = set(controlled)
        self.explicit = explicit
        self.checkpoints = checkpoints
        #: Stop condition for the current session (checked at every safe point).
        self.stop: Optional[Callable[[Env], bool]] = None
        #: The run's pending pauses, innermost last (a reaction or a chance node can wait inside a call).
        self.stack: List[Pause] = []
        self.running = False
        self._commands: "queue.SimpleQueue[Tuple[str, Any]]" = queue.SimpleQueue()
        self._events: "queue.SimpleQueue[Tuple[Any, ...]]" = queue.SimpleQueue()
        self._thread: Optional[threading.Thread] = None
        self._crash: Optional[BaseException] = None
        env.pilot = self
        if explicit or (playback is not None and playback.has_picks):
            env.world.chance_picker = self._pick

    # -- controller side ------------------------------------------------------------------------

    @property
    def pause(self) -> Optional[Pause]:
        return self.stack[-1] if self.stack else None

    def start(self) -> None:
        """Run until the first pause, the end, or the stop condition."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._session, name="fg-env-copy", daemon=True)
        self._thread.start()
        self._settle()

    def read(self, fn: Callable[[], Any]) -> Any:
        """``fn()`` evaluated on the run's thread while it is paused (directly when it is not running)."""
        if not self.running:
            return fn()
        self._commands.put(("do", fn))
        kind, value = self._events.get()
        if kind == "value":
            return value
        raise value

    def call(self, name: str, args: Any) -> Optional[ToolResult]:
        """A tool call in the paused turn; None when the call itself paused (a reaction or chance inside it)."""
        pause = self._expect("turn")
        assert pause.wake is not None
        wake = pause.wake
        self._commands.put(("do", lambda: wake.call(name, args)))
        return self._settle()

    def play(self, participant: Participant) -> None:
        """Let ``participant`` take the rest of the paused turn."""
        pause = self._expect("turn")
        assert pause.wake is not None
        wake = pause.wake
        self._commands.put(("do", lambda: participant(wake)))
        self._settle()

    def choose(self, index: int) -> Optional[ToolResult]:
        """Give the paused chance node its outcome."""
        self._expect("chance")
        self.stack.pop()
        self._commands.put(("resume", index))
        return self._settle()

    def release(self) -> None:
        """Hand every paused turn to the participants and let the run continue."""
        self.controlled.clear()
        self.explicit = False
        while self.stack:
            self.stack.pop()
            self._commands.put(("resume", None))
            self._settle()

    def close(self) -> None:
        """Stop a paused run's thread (the copy is discarded). Safe to call more than once."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        if self.stack:
            self._commands.put(("abort", None))
        if threading.current_thread() is not thread:
            thread.join()
        self.stack.clear()
        self.running = False

    def _expect(self, kind: str) -> Pause:
        pause = self.pause
        if pause is None:
            raise RuntimeError("nothing is waiting for a decision: the copy has "
                               + ("finished" if self.env.finished else "stopped; advance it first"))
        if pause.kind != kind:
            what = "a chance outcome (choose one)" if pause.kind == "chance" else "a tool call in a turn"
            raise RuntimeError(f"the copy is waiting for {what}, not a {kind}")
        return pause

    def _settle(self) -> Optional[ToolResult]:
        """Handle the run's messages until it waits for the controller again or its session ends.
        Returns the result of the call that completed, if one did."""
        result: Optional[ToolResult] = None
        while True:
            message = self._events.get()
            kind = message[0]
            if kind == "paused":
                self.stack.append(message[1])
                return result
            if kind == "value":
                result = message[1]
                top = self.stack[-1]
                if top.kind == "turn" and top.wake is not None and top.wake.done:
                    self.stack.pop()
                    self._commands.put(("resume", None))
                    continue
                return result
            if kind == "raised":
                raise message[1]
            if kind == "failed":
                error = message[1]
                self._drain()
                raise error
            self._finish()
            return result

    def _drain(self) -> None:
        while self._events.get()[0] != "finished":
            pass
        self._finish()

    def _finish(self) -> None:
        self.stack.clear()
        self.running = False
        if self._thread is not None:
            self._thread.join()
        crash, self._crash = self._crash, None
        if crash is not None:
            raise crash

    # -- run side --------------------------------------------------------------------------------

    def _session(self) -> None:
        try:
            self.env.run(stop=self.stop_here)
        except _Aborted:
            pass
        except BaseException as exc:  # an engine defect or a participant's BaseException: surfaced to the controller
            self._crash = exc
        finally:
            self._events.put(("finished",))

    def stop_here(self, env: Env) -> bool:
        """The stop condition the copy runs with: the session's ``stop``, and round-start checkpoints."""
        if self.checkpoints and not env._in_round:
            env.origin.checkpoint_due = True  # copies of this state start from this round, not from the beginning
        return self.stop is not None and self.stop(env)

    def take_turn(self, wake: Wake, inner: Participant) -> Any:
        turn = wake._turn
        seat = self.env.wake_for(wake)
        played = self.playback is not None and self.playback.play(seat)
        live = self.playback is None or self.playback.live(turn.number)
        controlled = False
        while live and not seat.done and turn.actor.id in self.controlled:
            controlled = True
            self._suspend(Pause("turn", wake=seat))
        if seat.done or (played and not controlled):
            return None  # a turn replayed to its end, or one that was in progress when the copy was taken
        participant = self.env.driver.bound(turn.actor) if controlled else inner  # type: ignore[attr-defined]
        return participant(wake)

    def _pick(self, node: ChanceNode) -> int:
        recorded = self.playback.next_pick() if self.playback is not None else None
        if recorded is None and self.explicit:
            recorded = self._suspend(Pause("chance", node=node))
        if recorded is None:
            return sample(self.env.world, node)
        self.env.origin.tape.pick(recorded)
        return int(recorded)

    def _suspend(self, pause: Pause) -> Any:
        self._events.put(("paused", pause))
        while True:
            kind, payload = self._commands.get()
            if kind == "resume":
                return payload
            if kind == "abort":
                raise _Aborted()
            try:
                value = payload()
            except _Aborted:
                raise
            except (RunError, ExprError) as exc:  # the rules failed: the run fails, as it would for a participant
                self._events.put(("failed", exc))
                raise
            except BaseException as exc:  # a mistake in the controller's own request: the run stays paused
                self._events.put(("raised", exc))
                continue
            self._events.put(("value", value))
