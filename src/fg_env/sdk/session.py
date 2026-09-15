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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .actions import ToolSpec

if TYPE_CHECKING:
    from .turn import Turn

__all__ = ["Wake", "ToolResult", "END_TURN"]

END_TURN = "end_turn"


@dataclass
class ToolResult:
    """What a tool call did. ``text`` is written for the agent; ``ended`` means the turn is over."""

    ok: bool
    text: str
    ended: bool = False
    data: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text


class Wake:
    """One agent's turn. Obtained from the runtime; never constructed directly."""

    def __init__(self, turn: "Turn"):
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
    def me(self) -> Dict[str, Any]:
        """A copy of this agent's own properties plus ``id``, ``name``, ``type`` and ``at``."""
        actor = self._turn.actor
        return {**actor.properties, "id": actor.id, "name": actor.name, "type": actor.entity_type, "at": actor.location_id}

    # -- what the agent reads ---------------------------------------------------

    @property
    def brief(self) -> str:
        """Static context: situation, rules, role. Identical across this agent's turns (cacheable)."""
        return self._turn.brief

    @property
    def update(self) -> str:
        """Dynamic context: time, why now, what happened since the last turn, declared views."""
        return self._turn.update

    @property
    def tools(self) -> List[ToolSpec]:
        """Tools legal right now. Recomputed after every call."""
        return self._offer(self._turn.tools())

    def _offer(self, tools: List[ToolSpec]) -> List[ToolSpec]:
        exposure = self._turn.exposure
        if exposure is not None and tools:
            with self._turn.env._lock:
                exposure.offered(tools)
        return tools

    def tools_for(self, provider: str = "anthropic") -> List[Dict[str, Any]]:
        """Tool definitions in a provider's format: ``anthropic`` or ``openai``."""
        converters: Dict[str, Callable[[ToolSpec], Dict[str, Any]]] = {
            "anthropic": ToolSpec.to_anthropic,
            "openai": ToolSpec.to_openai,
        }
        if provider not in converters:
            raise ValueError(f"unknown provider {provider!r} (anthropic, openai)")
        return [converters[provider](tool) for tool in self.tools]

    # -- acting ------------------------------------------------------------------------

    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> ToolResult:
        """Execute one tool call. Invalid calls cost nothing but a call and return what to fix."""
        return self._turn.call(name, args)

    def end(self) -> ToolResult:
        """Finish the turn."""
        return self._turn.call(END_TURN, {})

    def record_usage(self, *, llm_calls: int = 0, input_tokens: int = 0, output_tokens: int = 0,
                     cache_read_tokens: int = 0, cache_write_tokens: int = 0, llm_retries: int = 0,
                     forfeits: int = 0) -> None:
        """Add a model's real usage to the run's statistics (the built-in LLM participants call this)."""
        stats = self._turn.stats
        for name, value in (("llm_calls", llm_calls), ("input_tokens", input_tokens), ("output_tokens", output_tokens),
                            ("cache_read_tokens", cache_read_tokens), ("cache_write_tokens", cache_write_tokens),
                            ("llm_retries", llm_retries), ("forfeits", forfeits)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number ≥ 0, got {value!r}")
            setattr(stats, name, getattr(stats, name) + value)
        if self._turn.exposure is not None:
            with self._turn.env._lock:
                self._turn.exposure.used({"llm_calls": llm_calls, "input_tokens": input_tokens,
                                          "output_tokens": output_tokens, "cache_read_tokens": cache_read_tokens,
                                          "cache_write_tokens": cache_write_tokens})

    @property
    def done(self) -> bool:
        return self._turn.done

    @property
    def time_limit(self) -> Optional[float]:
        """Wall-clock seconds this turn may take, or None when it has no limit."""
        return self._turn.time_limit

    @property
    def time_left(self) -> Optional[float]:
        """Seconds left before the turn ends (None when it has no limit)."""
        return self._turn.time_left()

    @property
    def calls_left(self) -> int:
        return self._turn.calls_left

    @property
    def actions_left(self) -> int:
        return self._turn.actions_left

    def __repr__(self) -> str:
        return f"<Wake {self.entity_id} round {self.round} stage {self.stage!r}{' done' if self.done else ''}>"
