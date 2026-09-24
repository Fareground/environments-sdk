"""Piloting a copy of a run from outside: it pauses wherever a controlled agent must decide.

A :class:`Pilot` runs an environment on its own thread. Turns of the agents it controls, and
chance nodes when chance is explicit, suspend that thread; the controller (search code, a gym, a
lookahead) reads the paused world and sends tool calls or chance outcomes, which are executed on
the run's thread, inside the turn, exactly as a participant's would be. Everyone else is played by
participants. Turn time limits never run out in a copy (a paused search has no clock).

A copy of a piloted run (:meth:`Pilot.copy`) is a copy of its state: paused in a turn, the copy's round resumes there
and pauses at the same decision. Paused inside a decision — at a chance node inside its effects, or in an agent's
reaction inside its call, where no round can resume — the copy is the run as it was just before that decision (kept
when the decision began, whenever the run can pause inside one), taking the decision again: its steps so far and the
outcomes chosen in it play back (:mod:`.replay`), and it pauses where the original is.
"""
from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from ..effects.chance import ChanceNode, sample
from ..errors import RunError
from ..expr import ExprError
from ..expr.objects import Entity
from ..participants import Participant
from ..runtime.driving import Driver
from ..runtime.env import Env
from ..runtime.session import ToolResult, Wake
from ..runtime.turn import Turn
from ..runtime.turn_tools import HostWake
from .replay import Playback, Tape

__all__ = ["PilotedEnv", "Pilot", "Pause"]


class _Aborted(BaseException):
    """The copy was closed while its run waited for a decision."""


@dataclass
class Pause:
    """What a paused run is waiting for: a turn's next call, or a chance node's outcome."""

    kind: str
    wake: Wake | None = None
    node: ChanceNode | None = None


class PilotedEnv(Env):
    """A run whose turns go through a :class:`Pilot` (playback, then the controller or participants)."""

    pilot: Pilot | None

    def _assemble(self, like: Env | None) -> None:
        super()._assemble(like)
        self.pilot = None

    def _new_driver(self) -> Driver:
        return _SeatDriver(self)

    def wake_for(self, wake: Wake) -> Wake:
        """The wake a participant gets for this turn: offering the contract's in-turn host tools if any."""
        tools = self.driver.turn_tool_specs()
        if not tools:
            return wake
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
    def __init__(self, pilot: Pilot, inner: Participant):
        self.pilot = pilot
        self.inner = inner

    def __call__(self, wake: Wake) -> Any:
        return self.pilot.take_turn(wake, self.inner)


@dataclass
class _Before:
    """The run as it was when the decision now in flight began, never played on (see the module docstring): the copy,
    the turn that decided (None: the run was starting or continuing), and how many steps that turn had taken."""

    env: PilotedEnv
    turn: Turn | None
    steps: int


class Pilot:
    """Runs ``env`` with ``playback`` first, pausing for turns of ``controlled`` agents and, when
    ``explicit``, for chance nodes."""

    def __init__(self, env: PilotedEnv, *, playback: Playback | None = None, controlled: AbstractSet[str] = frozenset(),
                 explicit: bool = False, reactive: bool | None = None):
        self.env = env
        self._reactive = reactive
        self.playback = playback
        self.controlled = set(controlled)
        self.explicit = explicit
        #: Stop condition for the current session (checked at every safe point).
        self.stop: Callable[[Env], bool] | None = None
        #: The run's pending pauses, innermost last (a reaction or a chance node can wait inside a call).
        self.stack: list[Pause] = []
        self.running = False
        #: The run before the decision in flight, and what has happened in the decision since: the turns it woke and
        #: the chance outcomes chosen in it (what a copy paused inside it takes the decision again with).
        self._before: _Before | None = None
        self._woken: list[Turn] = []
        self._picks: list[int] = []
        self._commands: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._events: queue.SimpleQueue[tuple[Any, ...]] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._crash: BaseException | None = None
        env.pilot = self
        if explicit or (playback is not None and playback.has_picks):
            env.world.chance_picker = self._pick

    # -- controller side ------------------------------------------------------------------------

    @property
    def pause(self) -> Pause | None:
        return self.stack[-1] if self.stack else None

    @property
    def reactive(self) -> bool:
        """Whether the run's rules may wake an agent to react inside another's call (read from the contract once)."""
        if self._reactive is None:
            text = json.dumps(self.env.contract.model_dump(by_alias=True, exclude_defaults=True), default=str)
            self._reactive = '"wake"' in text and '"now"' in text
        return self._reactive

    def start(self) -> None:
        """Run until the first pause, the end, or the stop condition."""
        if self.running:
            return
        self.running = True
        if self.playback is None and self.env.state.cursor.waiting is None:
            self._decide(None)  # (a pilot taking a decision again starts inside it; one copied in a turn, at a pause)
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

    def call(self, name: str, args: Any) -> ToolResult | None:
        """A tool call in the paused turn; None when the call itself paused (a reaction or chance inside it)."""
        pause = self._expect("turn")
        assert pause.wake is not None
        wake = pause.wake
        self._decide(wake._turn)
        self._commands.put(("do", lambda: wake.call(name, args)))
        return self._settle()

    def play(self, participant: Participant) -> None:
        """Let ``participant`` take the rest of the paused turn."""
        pause = self._expect("turn")
        assert pause.wake is not None
        wake = pause.wake
        self._decide(wake._turn)
        self._commands.put(("do", lambda: participant(wake)))
        self._settle()

    def choose(self, index: int) -> ToolResult | None:
        """Give the paused chance node its outcome."""
        self._expect("chance")
        self.stack.pop()
        self._commands.put(("resume", index))
        return self._settle()

    def release(self) -> None:
        """Hand every paused turn to the participants and let the run continue."""
        self.controlled.clear()
        self.explicit = False
        self._before = None
        while self.stack:
            self.stack.pop()
            self._commands.put(("resume", None))
            self._settle()

    def copy(self) -> Pilot:
        """A pilot of a copy of the run now (see the module docstring), paused where this one is — or, when this one is
        not paused, stopped where it is (it plays on when started)."""
        env, pause = self.env, self.pause
        if pause is not None and (len(self.stack) > 1 or pause.wake is None or not self._in_round(pause.wake._turn)):
            copied = self._again()  # waiting inside a decision
        else:  # stopped, or waiting in a turn: the copy's round resumes there
            waiting = pause.wake._turn if pause is not None and pause.wake is not None else None
            copied = Pilot(env.copy(waiting=waiting), controlled=self.controlled, explicit=self.explicit,
                           reactive=self._reactive)
        copied.stop = self.stop
        if pause is not None:
            copied.start()
        return copied

    def _again(self) -> Pilot:
        """A pilot of the run before the decision in flight, taking it again to where this one waits inside it."""
        before = self._before
        if before is None:
            raise RunError("this copy waits inside a decision it cannot take again (it was not expected to pause "
                           "there): copy it at the turn before", "clone")
        tape = Tape()
        turns = [] if before.turn is None else [before.turn]
        for turn in (*turns, *self._woken):
            done = before.steps if turn is before.turn else 0
            tape.turns[turn.number] = (turn.actor.id, list(turn.steps[done:]))
        if before.turn is not None:
            tape.open.add(before.turn.number)
        tape.picks = list(self._picks)
        env: PilotedEnv = before.env.copy(waiting=before.turn)
        copied = Pilot(env, playback=Playback(tape, before.env.state.turn_count), controlled=self.controlled,
                       explicit=self.explicit, reactive=self._reactive)
        copied._before = before
        return copied

    def _decide(self, turn: Turn | None) -> None:
        """A decision begins — ``turn``'s call, or the run starting or going on — from the run as it is now: keep a
        copy of it when the run may pause inside the decision, where no round resumes (a chance node chosen explicitly,
        a controlled agent reacting)."""
        env = self.env
        inside = self.stack[:-1] or (turn is not None and not self._in_round(turn))
        if inside or not (self.explicit or (self.controlled and self.reactive)):
            return  # already inside a decision (a reaction's call is part of the call it reacts to), or no need
        with env._lock:
            self._before = _Before(env.copy(waiting=turn), turn, len(turn.steps) if turn is not None else 0)
        self._woken, self._picks = [], []

    def _in_round(self, turn: Turn) -> bool:
        """Whether ``turn`` is one the round plays (not a reaction inside another's call): a copy's round resumes in
        it."""
        state = self.env.state
        return turn is state.cursor.turn or turn in state.staged

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

    def abandon(self) -> None:
        """Ask a paused run's thread to stop, without waiting for it: what a copy nobody holds any more does when it
        is garbage collected, where no thread may be joined and no rule evaluated (the thread unwinds on its own)."""
        thread = self._thread
        if thread is not None and thread.is_alive() and self.stack:
            self._commands.put(("abort", None))

    def _expect(self, kind: str) -> Pause:
        pause = self.pause
        if pause is None:
            raise RuntimeError("nothing is waiting for a decision: the copy has "
                               + ("finished" if self.env.finished else "stopped; advance it first"))
        if pause.kind != kind:
            what = "a chance outcome (choose one)" if pause.kind == "chance" else "a tool call in a turn"
            raise RuntimeError(f"the copy is waiting for {what}, not a {kind}")
        return pause

    def _settle(self) -> ToolResult | None:
        """Handle the run's messages until it waits for the controller again or its session ends.
        Returns the result of the call that completed, if one did."""
        result: ToolResult | None = None
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
        """The stop condition the copy runs with: the session's ``stop``."""
        return self.stop is not None and self.stop(env)

    def take_turn(self, wake: Wake, inner: Participant) -> Any:
        turn = wake._turn
        if self._before is not None and turn is not self._before.turn:
            self._woken.append(turn)
        seat = self.env.wake_for(wake)
        played = self.playback is not None and self.playback.play(seat)
        live = self.playback is None or self.playback.live(turn.number)
        controlled = False
        while live and not seat.done and turn.actor.id in self.controlled:
            controlled = True
            self._suspend(Pause("turn", wake=seat))
        if seat.done or (played and not controlled):
            return None  # a turn replayed to its end, or one that was in progress when the decision began
        participant = self.env.driver.bound(turn.actor) if controlled else inner  # type: ignore[attr-defined]
        return participant(wake)

    def _pick(self, node: ChanceNode) -> int:
        recorded = self.playback.next_pick() if self.playback is not None else None
        if recorded is None and self.explicit:
            recorded = self._suspend(Pause("chance", node=node))
        if recorded is None:
            return sample(self.env.world, node)
        self._picks.append(int(recorded))
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
