"""The run behind a game state: stepped on the caller's own thread, or piloted on a thread of its own.

Both drive the same engine through the same turns, calls and chance nodes, and a state reads and decides
through either in the same way (:class:`Run`), and a clone of either is a copy of its run's state. Stepping
(:mod:`fg_env.copying.stepping`) is the fast path: no thread hand-offs. A game is stepped unless its contract needs
what only a piloted run carries — time limits, in-turn host tools, or callable participants for the other agents. A
stepped state that meets such a need later (a seat woken to react inside another agent's call) goes on as a piloted
run rebuilt from its decisions, and the game's later states start piloted.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from ..copying.branch import Branch
from ..copying.pilot import Pause
from ..runtime.session import ToolResult

if TYPE_CHECKING:
    from .game import Game

__all__ = ["Run", "ThreadedRun", "can_step", "replayed"]


class Run(Protocol):
    """What a game state needs of its run."""

    prefetch: Callable[[Any], Any] | None

    @property
    def pause(self) -> Pause | None: ...

    def read(self, fn: Callable[[Any], Any]) -> Any: ...

    def read_prefetched(self) -> Any: ...

    def call(self, name: str, args: Any) -> ToolResult | None: ...

    def choose(self, index: int) -> ToolResult | None: ...

    def clone(self) -> Run: ...

    def result(self) -> Any: ...

    def entity(self, entity_id: str) -> Any: ...

    def close(self) -> None: ...


class ThreadedRun:
    """A game state's run piloted on its own thread."""

    def __init__(self, branch: Branch, prefetch: Callable[[Any], Any] | None):
        self._branch = branch
        self._pilot = branch._pilot
        self.prefetch = prefetch

    @property
    def pause(self) -> Pause | None:
        return self._pilot.pause

    def read(self, fn: Callable[[Any], Any]) -> Any:
        env = self._pilot.env
        return self._pilot.read(lambda: fn(env))

    def read_prefetched(self) -> Any:
        return self.read(self.prefetch) if self.prefetch is not None else None

    def call(self, name: str, args: Any) -> ToolResult | None:
        return self._pilot.call(name, args)

    def choose(self, index: int) -> ToolResult | None:
        return self._pilot.choose(index)

    def clone(self) -> ThreadedRun:
        return ThreadedRun(self._branch.clone(), self.prefetch)

    def result(self) -> Any:
        return self._branch.result()

    def entity(self, entity_id: str) -> Any:
        return self._branch.entity(entity_id)

    def close(self) -> None:
        self._branch.close()


def can_step(game: Game) -> bool:
    """Whether the game's states can be stepped (see the module docs)."""
    root, others = game._root, game._others
    named = others is None or isinstance(others, str) or (
        isinstance(others, Mapping) and all(isinstance(value, str) for value in others.values()))
    return named and root.time_limit is None and not root.driver.turn_tool_specs()


def replayed(game: Game, history: Sequence[Mapping[str, Any]]) -> ThreadedRun:
    """A piloted run that has taken the decisions of ``history`` from the initial state (the game's later states
    start piloted too)."""
    game._stepped = False
    run = game._piloted_start()
    try:
        for entry in history:
            if "chance" in entry:
                run.choose(entry["chance"])
            else:
                run.call(entry["tool"], entry["args"])
    except BaseException:
        run.close()
        raise
    return run
