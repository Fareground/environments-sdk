"""Driving participants: whoever takes a turn runs inline, in a thread, or on an event loop.

A participant is a plain function, a function that returns an awaitable, or an ``async def``
(also as an object's ``__call__``). Plain functions run inline on the engine's thread — the fast,
thread-free path coded crowds take — unless the turn has a time limit or runs alongside others
in a simultaneous stage; then they run in a daemon thread. Awaitables run on an event loop: the
caller's own loop under :meth:`Env.arun`, otherwise one shared background loop.

The engine waits on a condition bound to the run's lock. Waiting releases the lock, so a turn's
calls go through even when the wait happens inside another agent's call (reactions). A turn past
its deadline is closed at once: its later calls are refused and the engine moves on. A thread
cannot be killed, so a hung plain function keeps its (daemon) thread until it returns; an async
participant is cancelled. Turns that finish in time are exactly as deterministic as inline ones:
every draw comes from the turn's own stream and simultaneous choices commit in turn order.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
from collections import deque
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

from ..entity import Entity
from .errors import RunError
from .expr import ExprError
from .participants import Idle, Participant, resolve_participant
from .session import END_TURN, Wake

if TYPE_CHECKING:
    from .runtime import Env
    from .turn import Turn

__all__ = ["Driver", "is_async", "background_loop", "run_on_worker"]

_LOOP_LOCK = threading.Lock()
_IDLE = Idle()
_LOOPS: Dict[int, asyncio.AbstractEventLoop] = {}


def background_loop() -> asyncio.AbstractEventLoop:
    """The process's shared event loop for async participants of synchronous runs (started on first use)."""
    pid = os.getpid()  # a forked worker cannot use its parent's loop thread
    with _LOOP_LOCK:
        loop = _LOOPS.get(pid)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="fg-env-participants", daemon=True).start()
            _LOOPS.clear()
            _LOOPS[pid] = loop
        return loop


async def run_on_worker(play: Callable[[asyncio.AbstractEventLoop, Callable[[Any], bool]], Any],
                        stop: Optional[Callable[[Any], bool]]) -> Any:
    """Await the blocking ``play(loop, halt)`` running in a worker thread, leaving this loop free for the
    participants it schedules. Cancelling the await makes ``halt`` true, so the run stops at its next safe point."""
    loop = asyncio.get_running_loop()
    landed: "asyncio.Future[Any]" = loop.create_future()
    cancelled = threading.Event()

    def halt(env: Any) -> bool:
        return cancelled.is_set() or (stop is not None and stop(env))

    def work() -> None:
        outcome: Any = None
        error: Optional[BaseException] = None
        try:
            outcome = play(loop, halt)
        except BaseException as exc:  # handed to the awaiting task
            error = exc
        try:
            loop.call_soon_threadsafe(_resolve, landed, outcome, error)
        except RuntimeError:
            pass  # the loop closed before the run finished: nobody is waiting

    threading.Thread(target=work, name="fg-env-run", daemon=True).start()
    try:
        return await landed
    except asyncio.CancelledError:
        cancelled.set()
        raise


def _resolve(future: "asyncio.Future[Any]", result: Any, error: Optional[BaseException]) -> None:
    if future.cancelled():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


def is_async(participant: Any) -> bool:
    """Whether calling ``participant`` gives an awaitable by declaration: an ``async def``, an object with an
    async ``__call__``, or a wrapper whose ``__wrapped__`` is one."""
    for _ in range(8):
        if participant is None:
            return False
        if inspect.iscoroutinefunction(participant) or inspect.iscoroutinefunction(getattr(participant, "__call__", None)):
            return True
        participant = getattr(participant, "__wrapped__", None)
    return False


def _is_async_generator(participant: Any) -> bool:
    return inspect.isasyncgenfunction(participant) or inspect.isasyncgenfunction(getattr(participant, "__call__", None))


class _Flight:
    """One turn whose participant runs off the engine's thread."""

    __slots__ = ("turn", "alone", "landed", "error", "future", "cancelled")

    def __init__(self, turn: "Turn", alone: bool):
        self.turn = turn
        #: Runs while no other flight does (a participant not marked concurrent, or a stage played in order).
        self.alone = alone
        self.landed = False
        self.error: Optional[BaseException] = None
        self.future: Optional[Future] = None
        self.cancelled = False


class Driver:
    """Binds participants to agents and plays their turns."""

    #: Turns with a time limit run against the wall clock (copies of a run for search switch it off).
    timed = True

    def __init__(self, env: "Env"):
        self.env = env
        self.spec: Dict[str, Any] = {}
        self._resolved: Dict[str, Participant] = {}
        #: The loop async participants run on: the caller's under ``arun``, else the shared background loop.
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    # -- binding ----------------------------------------------------------------------

    def bind(self, participants: Any) -> None:
        if participants is None:
            return
        if callable(participants) or isinstance(participants, str):
            participants = {"*": participants}
        if not isinstance(participants, Mapping):
            raise TypeError("participants must be a callable, a string, or a mapping")
        env = self.env
        known = set(env.contract.types) | set(env.world.entities) | {"*"}
        for key, value in participants.items():
            if key not in known:
                raise ValueError(f"participants key '{key}' is not an entity id, a type, or '*'")
            if not callable(value):
                resolve_participant(value, env.contract, 0)  # an unknown name fails now, not mid-run
            elif _is_async_generator(value):
                raise TypeError(f"participant for '{key}' is an async generator; a participant plays one turn per "
                                "call — use a plain function or an async def")
        self.spec = dict(participants)
        self._resolved.clear()

    def participant(self, actor: Entity) -> Participant:
        budget = self.env.budget
        if budget is not None and budget.exhausted is not None and budget.on_exhaust == "idle":
            return _IDLE  # the run's budget ran out: agents take no more actions
        cached = self._resolved.get(actor.id)
        if cached is not None:
            return cached
        env, spec = self.env, self.spec
        lineage = list(reversed(env.contract.lineage(actor.entity_type)))  # most specific type first
        value = spec.get(actor.id)
        if value is None:
            value = next((spec[kind] for kind in lineage if kind in spec), spec.get("*"))
        if value is None:
            value = next((env.contract.types[kind].policy for kind in lineage if env.contract.types[kind].policy),
                         None) or "random"
        participant = resolve_participant(value, env.contract, env.seeds.derive("participant"))
        participant = self._with_turn_tools(participant)
        self._resolved[actor.id] = participant
        return participant

    def turn_tool_specs(self) -> Dict[str, Any]:
        """The contract's in-turn host tools (recall, note, host services), by name."""
        tools = self.__dict__.get("_turn_tools")
        if tools is None:
            from .host.turn_tools import turn_tools

            tools = self.__dict__["_turn_tools"] = turn_tools(self.env.contract)
        return tools

    def _with_turn_tools(self, participant: Participant) -> Participant:
        tools = self.turn_tool_specs()
        if not tools:
            return participant
        from .host.turn_tools import offer

        return offer(participant, tools)

    # -- playing turns ----------------------------------------------------------------------

    def drive(self, turns: Sequence["Turn"], together: bool = False) -> None:
        """Play ``turns``: one at a time, or — ``together``, a simultaneous stage — concurrently where the
        participants allow it. Participant failures raise :class:`RunError` once every turn has stopped."""
        env = self.env
        auto = [turn for turn in turns if turn.stage.auto and not turn.staged]
        played = [turn for turn in turns if turn not in auto or not self._auto(turn)]
        chosen = [(turn, self.participant(turn.actor)) for turn in played]
        concurrent = sum(1 for _, p in chosen if getattr(p, "concurrent", True))
        threaded = together and env.parallel > 1 and concurrent > 1
        queue: Deque[Tuple["Turn", Participant, bool]] = deque()
        if together:
            env.origin.staged = list(turns)  # sealed choices still being made (read by game states)
        try:
            for turn, participant in chosen:
                alone = not (threaded and getattr(participant, "concurrent", True))
                if turn.time_limit is not None or is_async(participant) or not alone:
                    queue.append((turn, participant, alone))
                    continue
                rng = self._rng(turn)
                answer = self._inline(turn, participant, rng)
                if inspect.isawaitable(answer):  # a plain function that handed back a coroutine: await it now
                    flight = _Flight(turn, alone=True)
                    self._submit(flight, answer, rng)
                    self._fly(deque(), [flight])
            self._fly(queue, [])
            for turn in played:
                if not turn.staged:
                    turn.settle_at_end()
        finally:
            for turn in played:
                self.finish(turn)
            if together:
                env.origin.staged = []

    def finish(self, turn: "Turn") -> None:
        """Close a played turn: no more calls, its statistics added to the run's."""
        env = self.env
        with env._lock:
            turn.done = True
            env.origin.tape.closed(turn.number)
            if turn.stats.actions == 0 and not turn.intents:
                turn.stats.idle_turns += 1
            env._tally(turn.actor.id, turn.stats)
            if turn.exposure is not None and not turn.staged:  # simultaneous turns close once their choices commit
                turn.exposure.close(turn)

    def _rng(self, turn: "Turn") -> Any:
        return self.env.seeds.rng("turn", turn.round, turn.number)

    def _inline(self, turn: "Turn", participant: Participant, rng: Any) -> Any:
        with self.env.world.turn_context(rng, turn.pending):
            try:
                return participant(Wake(turn))
            except (RunError, ExprError):
                raise
            except Exception as exc:
                raise _failure(turn, exc) from exc

    def _fly(self, queue: Deque[Tuple["Turn", Participant, bool]], flights: List[_Flight]) -> None:
        """Launch queued turns (at most ``parallel`` at once; a turn that must run alone waits for the others)
        and wait until every flight has landed or been closed, enforcing deadlines. Waiting releases the run's lock."""
        env = self.env
        signal = env._signal
        failures: List[_Flight] = []
        with signal:
            try:
                while queue or flights:
                    while queue and len(flights) < env.parallel and not failures:
                        if flights and (queue[0][2] or any(flight.alone for flight in flights)):
                            break
                        flights.append(self._launch(*queue.popleft()))
                    if failures:
                        for turn, _, _ in queue:
                            turn.done = True  # never started: the run is failing
                        queue.clear()
                    now = time.monotonic()
                    wait: Optional[float] = None
                    for flight in list(flights):
                        turn = flight.turn
                        if not flight.landed and (failures or turn.expired(now)):
                            self._close(flight)
                        if (flight.landed or turn.done and flight.cancelled) and turn.busy == 0:
                            flights.remove(flight)
                            if flight.error is not None:
                                failures.append(flight)
                        elif not flight.landed and not flight.cancelled and turn.deadline is not None:
                            left = max(0.0, turn.deadline - now)
                            wait = left if wait is None else min(wait, left)
                    if flights:
                        signal.wait(wait)
            except BaseException:
                for flight in flights:
                    self._close(flight)
                raise
        if failures:
            first = min(failures, key=lambda f: f.turn.number)
            error = first.error
            if isinstance(error, (RunError, ExprError)):
                raise error
            assert error is not None
            raise _failure(first.turn, error) from error

    def _launch(self, turn: "Turn", participant: Participant, alone: bool) -> _Flight:
        flight = _Flight(turn, alone)
        if self.timed:
            turn.start_clock()
        rng = self._rng(turn)
        if is_async(participant):
            try:
                with self.env.world.turn_context(rng, turn.pending):
                    answer = participant(Wake(turn))  # an async def runs nothing until awaited
            except BaseException as exc:
                self._land(flight, exc)
                return flight
            self._submit(flight, answer, rng)
        else:
            threading.Thread(target=self._thread, args=(flight, participant, rng), daemon=True,
                             name=f"fg-env-turn-{turn.actor.id}").start()
        return flight

    def _thread(self, flight: _Flight, participant: Participant, rng: Any) -> None:
        turn = flight.turn
        try:
            with self.env.world.turn_context(rng, turn.pending):
                answer = participant(Wake(turn))
        except BaseException as exc:  # handed to the engine's thread, which reports it
            self._land(flight, exc)
            return
        if inspect.isawaitable(answer):
            self._submit(flight, answer, rng)
        else:
            self._land(flight, None)

    def _submit(self, flight: _Flight, answer: Any, rng: Any) -> None:
        """Run an awaitable on the participants' loop; the flight lands when it completes."""
        turn, world = flight.turn, self.env.world
        loop = self.loop or background_loop()
        try:
            running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _discard(answer)
            self._land(flight, RunError(
                f"participant for {turn.actor.id} is async, and this run was started from inside the event loop "
                "its participants need; start it with `await env.arun(...)`", f"participant:{turn.actor.id}"))
            return

        async def play() -> None:
            with world.turn_context(rng, turn.pending):
                await answer

        coroutine = play()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError as exc:  # the loop is closed
            coroutine.close()
            _discard(answer)
            self._land(flight, exc)
            return
        with self.env._lock:
            flight.future = future
            if flight.cancelled:
                future.cancel()
        future.add_done_callback(lambda done: self._landed(flight, done))

    def _landed(self, flight: _Flight, future: Future) -> None:
        if future.cancelled():
            error: Optional[BaseException] = None if flight.cancelled else \
                RunError(f"participant for {flight.turn.actor.id} was cancelled", f"participant:{flight.turn.actor.id}")
        else:
            error = future.exception()
        self._land(flight, error)

    def _land(self, flight: _Flight, error: Optional[BaseException]) -> None:
        with self.env._signal:
            if error is not None and not flight.cancelled and not flight.turn.timed_out:
                flight.error = error
            flight.landed = True
            self.env._signal.notify_all()

    def _close(self, flight: _Flight) -> None:
        """Stop waiting for a flight: its turn takes no more calls; an async participant is cancelled."""
        flight.turn.done = True
        if flight.cancelled:
            return
        flight.cancelled = True
        if flight.future is not None:
            flight.future.cancel()

    # -- auto turns ---------------------------------------------------------------------------

    def _auto(self, turn: "Turn") -> bool:
        """Play a trivial turn without the agent: the only legal action when it takes no arguments,
        or nothing when no action is legal. False when the agent has a real choice."""
        env = self.env
        with env.world.turn_context(self._rng(turn), turn.pending):
            acts = [tool for tool in turn.tools() if tool.kind == "act"]
            if len(acts) > 1 or (acts and acts[0].input_schema.get("properties")):
                return False
            if acts:
                turn.call(acts[0].name, {})
            if not turn.done:
                turn.call(END_TURN, {})
        turn.stats.wakes = 0
        turn.stats.auto_turns = 1
        turn.exposure = None  # the agent was never woken, so it was shown nothing
        self.finish(turn)
        return True


def _failure(turn: "Turn", exc: BaseException) -> RunError:
    return RunError(f"participant for {turn.actor.id} raised {type(exc).__name__}: {exc}", f"participant:{turn.actor.id}")


def _discard(answer: Any) -> None:
    close = getattr(answer, "close", None)
    if inspect.iscoroutine(answer) and callable(close):
        close()  # never awaited: close it so it does not linger
