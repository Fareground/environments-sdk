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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .actions import ToolSpec

if TYPE_CHECKING:
    from .runtime import _Turn

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

    def __init__(self, turn: "_Turn"):
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
        return self._turn.tools()

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

    @property
    def done(self) -> bool:
        return self._turn.done

    @property
    def calls_left(self) -> int:
        return self._turn.calls_left

    @property
    def actions_left(self) -> int:
        return self._turn.actions_left

    def __repr__(self) -> str:
        return f"<Wake {self.entity_id} round {self.round} stage {self.stage!r}{' done' if self.done else ''}>"
