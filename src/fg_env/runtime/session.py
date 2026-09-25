"""The turn session an agent drives: read the picture, call tools, end the turn.

A participant receives a :class:`Wake`. It reads ``wake.brief`` (static, cache it) and
``wake.update`` (what is new), then calls tools until the turn is over::

    def my_agent(wake):
        result = wake.call("buy", {"offer": "house_blend", "qty": 2})
        if not result.ok:
            wake.call("buy", {"offer": "house_blend", "qty": 1})   # the text says what to fix
        wake.end()

For an LLM, ``wake.tools_for("anthropic")`` / ``"openai"`` gives provider tool
definitions and ``wake.call(name, args)`` executes the model's tool call; feed
``result.text`` back as the tool result.

A participant may also be an ``async def`` (or return an awaitable): the engine awaits it, and
runs the async participants of a simultaneous stage concurrently.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..actions.params import REFUSED_ARGS, unbounded
from ..assets.delivery import Attachment
from ..assets.intake import intake
from ..information.schemas import END_TURN, ToolSpec
from .facts import Usage

if TYPE_CHECKING:
    from ..copying.branch import Branch
    from .turn import Turn

__all__ = ["Wake", "ToolResult", "END_TURN", "without_lookahead"]


@dataclass
class ToolResult:
    """What a tool call did. ``text`` is written for the agent; ``ended`` means the turn is over."""

    ok: bool
    text: str
    ended: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    #: Files delivered with the result (an action's `attach`, a view's, or the asset properties inspect shows).
    attachments: list[Attachment] = field(default_factory=list)

    def __str__(self) -> str:
        return self.text


class Wake:
    """One agent's turn. Obtained from the runtime; never constructed directly."""

    def __init__(self, turn: Turn):
        self._turn = turn

    # -- who / when / why -----------------------------------------------------

    @property
    def entity_id(self) -> str:
        return self._turn.actor.id

    @property
    def name(self) -> str:
        return self._turn.actor.name

    @property
    def type(self) -> str:
        return self._turn.actor.entity_type

    @property
    def round(self) -> int:
        return self._turn.round

    @property
    def stage(self) -> str:
        return self._turn.stage.name

    @property
    def reason(self) -> str:
        return self._turn.reason

    @property
    def me(self) -> dict[str, Any]:
        """A copy of this agent's own properties plus ``id``, ``name``, ``type`` and ``at``: changing it changes nothing
        in the world. Read under the run's gate, so it never catches another agent's sealed choices being tried."""
        actor = self._turn.actor
        with self._turn.gate:
            return {**_copy(dict(actor.properties)), "id": actor.id, "name": actor.name, "type": actor.entity_type,
                    "at": actor.location_id}

    # -- what the agent reads ---------------------------------------------------

    @property
    def brief(self) -> str:
        """Static context: situation, rules, role. Identical across this agent's turns (cacheable)."""
        if self._turn._brief is None:
            self._turn.record("brief")
        return self._turn.brief

    @property
    def update(self) -> str:
        """Dynamic context: time, why now, what happened since the last turn, declared views."""
        if self._turn._update is None:
            self._turn.record("update")
        return self._turn.update

    @property
    def attachments(self) -> list[Attachment]:
        """The files delivered with the brief and the update (reading them reads both): each has ``type``, ``name``,
        ``media_type``, ``caption``, ``alt``, ``size``, ``hash``, ``read()`` for its bytes and ``text()`` for text
        files."""
        self.brief
        self.update
        return self._turn.attachments()

    @property
    def tools(self) -> list[ToolSpec]:
        """Tools legal right now. Recomputed after every call."""
        if not self._turn._offered:
            self._turn.record("tools")
        return self._offer(self._turn.tools())

    def _offer(self, tools: list[ToolSpec]) -> list[ToolSpec]:
        exposure = self._turn.exposure
        if exposure is not None and tools:
            with self._turn.gate:
                exposure.offered(tools)
        return tools

    def tools_for(self, provider: str = "anthropic") -> list[dict[str, Any]]:
        """Tool definitions in a provider's format: ``anthropic`` or ``openai``."""
        converters: dict[str, Callable[[ToolSpec], dict[str, Any]]] = {
            "anthropic": ToolSpec.to_anthropic,
            "openai": ToolSpec.to_openai,
        }
        if provider not in converters:
            raise ValueError(f"unknown provider {provider!r} (anthropic, openai)")
        return [converters[provider](tool) for tool in self.tools]

    # -- acting ------------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        """Execute one tool call. Invalid calls cost nothing but a call and return what to fix."""
        turn = self._turn
        problem = unbounded(args)
        if problem is not None:  # refused before anything copies or walks them: the call is invalid, saying why
            args = {REFUSED_ARGS: problem}
        with turn.gate:  # a call made after the deadline is refused, so it is no step on the tape
            if turn.refusal() is None:
                args = intake(turn, name, args)  # submitted files are stored first: the tape holds their ids
                turn.record("call", name, _copy(args))
            return turn.call(name, args)

    def upload(self, source: bytes | str | os.PathLike[str], name: str | None = None) -> str:
        """Store a file for this agent — bytes, or a path your own code chose — and return its id, to pass as a
        `file` argument (``{"asset": id}``). Its kind is recognised from its bytes; it is untrusted like any
        participant text."""
        from ..assets.intake import upload

        return upload(self._turn, source, name)

    def end(self) -> ToolResult:
        """Finish the turn; a normally finished turn needs no further tool call.

        Timeouts and externally closed turns retain their refusal. Explicit
        ``call("end_turn")`` still follows the tool protocol, including recording.
        """
        turn = self._turn
        with turn.gate:
            if turn.done and not turn.closed and not turn.expired():
                return ToolResult(True, "Turn already ended.", True)
            return self.call(END_TURN, {})

    def clone(self, *, participants: Any = None, seed: int | None = None, same_luck: bool = False) -> Branch:
        """A private copy of the whole run, paused exactly here in this turn, to look ahead on.

        Try tool calls on it (``branch.call``), let it play on (``branch.run`` or ``branch.advance``) and
        read the outcome; the real run's state, random streams, turn numbers and log never change. The
        copy pauses for this agent's turns; everyone else is played by ``participants`` (default: the
        run's named participants, else each type's policy, else random). In a simultaneous stage the others'
        sealed choices not yet committed are not in the copy: its participants choose for them. Wall-clock time
        limits do not apply in a copy.

        Its luck is fresh: draws from here on come from a stream derived from this turn (or ``seed``), so
        looking ahead never reveals the real run's future draws, and every clone taken in this turn shares
        that stream (compare moves under the same luck). ``same_luck=True`` keeps the real run's streams.
        The copy holds the whole world, hidden state included: honest search in a game of hidden
        information reads only what the agent may see. A turn taken as a reaction inside another agent's call is not
        cloned (a copy of the run cannot begin inside that call): clone the turn it reacts to.
        """
        from ..copying.branch import clone_turn

        return clone_turn(self._turn, participants=participants, seed=seed, same_luck=same_luck)

    def record_usage(self, *, llm_calls: int = 0, input_tokens: int = 0, output_tokens: int = 0,
                     cache_read_tokens: int = 0, cache_write_tokens: int = 0, llm_retries: int = 0,
                     forfeits: int = 0, truncated: int = 0, refusals: int = 0, out_of_steps: int = 0,
                     no_tool_replies: int = 0, unreported_usage: int = 0, too_long: int = 0) -> None:
        """Add a model's real usage to the run's statistics (the built-in LLM participants call this). Usage reported
        after the turn is over (it ran out of time) still counts toward the statistics and the budget; usage that
        spends the run's token budget ends every turn in play (the built-in LLM participants then make no more calls).
        ``input_tokens`` are the fresh ones, not read from the provider's prompt cache (``cache_read_tokens``; a token
        budget counts those at a tenth). ``truncated`` counts replies cut off at the model's output limit,
        ``refusals`` replies the provider refused to give, ``out_of_steps`` turns the participant's own call limit
        ended, ``no_tool_replies`` turns the model ended answering in text without a tool call. Each of the last four
        counts a turn with an action open and none taken as failed. ``unreported_usage`` counts calls whose provider
        reported no usage (their input tokens are the prompt's estimated size); ``too_long``, forfeits whose prompt was
        longer than the model takes."""
        counts = (("llm_calls", llm_calls), ("input_tokens", input_tokens), ("output_tokens", output_tokens),
                  ("cache_read_tokens", cache_read_tokens), ("cache_write_tokens", cache_write_tokens),
                  ("llm_retries", llm_retries), ("forfeits", forfeits), ("truncated", truncated),
                  ("refusals", refusals), ("out_of_steps", out_of_steps), ("no_tool_replies", no_tool_replies),
                  ("unreported_usage", unreported_usage), ("too_long", too_long))
        for name, value in counts:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number ≥ 0, got {value!r}")
        turn = self._turn
        reported = {name: value for name, value in counts if value}
        shown = {name: value for name, value in reported.items() if name in _SHOWN_USAGE}
        with turn.gate:
            turn.note(Usage(reported))  # a turn over and counted adds it to the run's totals (it still cannot act)
            if turn.tallied:
                if turn.exposure is not None:
                    turn.exposure.used(shown, late=True)
                return
            turn.record("usage", reported)
            if turn.exposure is not None:
                turn.exposure.used(shown)
            budget = turn.env.budget
            if budget is not None and budget.tokens_spent(turn.env, turn):
                for playing in (*turn.env.state.staged, turn):  # the budget is spent: every turn in play ends here
                    playing.done = True

    @property
    def done(self) -> bool:
        return self._turn.done

    @property
    def time_limit(self) -> float | None:
        """Wall-clock seconds this turn may take, or None when it has no limit."""
        return self._turn.time_limit

    @property
    def time_left(self) -> float | None:
        """Seconds left before the turn ends (None when it has no limit)."""
        return self._turn.time_left()

    @property
    def calls_left(self) -> int:
        return self._turn.ledger.calls_left

    @property
    def actions_left(self) -> int:
        return self._turn.ledger.actions_left

    def __repr__(self) -> str:
        return f"<Wake {self.entity_id} round {self.round} stage {self.stage!r}{' done' if self.done else ''}>"


#: The reported usage an exposure record shows.
_SHOWN_USAGE = ("llm_calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "truncated")


def _copy(value: Any) -> Any:
    """A deep copy of plain data: call arguments as recorded on the tape (the caller may reuse its own objects), or
    properties handed to a participant."""
    if isinstance(value, list):
        return [_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    return value


class _NoLookahead(Wake):
    """A turn whose participant may not look ahead (a tournament's or evaluation's by default): :meth:`clone` is
    refused."""

    def clone(self, *, participants: Any = None, seed: int | None = None, same_luck: bool = False) -> Branch:
        raise RuntimeError("wake.clone is off for this participant (lookahead=True allows it): a copy of the run "
                           "holds the whole world, hidden state and future luck included")


class _WithoutLookahead:
    """A participant whose turns come as :class:`_NoLookahead` wakes."""

    def __init__(self, participant: Any):
        from .driving import runs_concurrently

        self.__wrapped__ = participant  # read to tell whether it is async (see runtime/driving.py)
        self.concurrent = runs_concurrently(participant)

    def __call__(self, wake: Wake) -> Any:
        return self.__wrapped__(_NoLookahead(wake._turn))


def without_lookahead(participant: Any) -> Any:
    """``participant`` playing with `wake.clone` refused, so an entrant written by someone else cannot read the run's
    hidden state or future luck through a copy of it; a built-in participant given by name is returned as it is."""
    return _WithoutLookahead(participant) if callable(participant) else participant

