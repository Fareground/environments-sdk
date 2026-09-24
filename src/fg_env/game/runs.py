"""The run behind a game state: stepped on the caller's own thread, or piloted on a thread of its own.

Both drive the same engine through the same turns, calls and chance nodes, and a state reads and decides
through either in the same way (:class:`Run`). Stepping (:mod:`fg_env.copying.stepping`) is the fast path: no
thread hand-offs, and a clone copies the world directly instead of replaying the game. A game is stepped
unless its contract needs what only a piloted run carries — hosts, a budget, time limits, atomic or scheduled
stages, physics, a space, in-turn host tools, or callable participants for the other agents. A stepped state
that meets such a need later (a seat woken to react inside another agent's call, a world a direct copy does not
carry) goes on as a piloted run rebuilt from its decisions, and the game's later states start piloted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Protocol, Sequence

from ..copying.branch import Branch
from ..host.hosts import hosts_for
from ..copying.pilot import Pause
from ..runtime.session import ToolResult

if TYPE_CHECKING:
    from .game import Game

__all__ = ["Run", "ThreadedRun", "can_step", "replayed"]


class Run(Protocol):
    """What a game state needs of its run."""

    prefetch: Optional[Callable[[Any], Any]]

    @property
    def pause(self) -> Optional[Pause]: ...

    def read(self, fn: Callable[[Any], Any]) -> Any: ...

    def read_prefetched(self) -> Any: ...

    def call(self, name: str, args: Any) -> Optional[ToolResult]: ...

    def choose(self, index: int) -> Optional[ToolResult]: ...

    def clone(self) -> "Run": ...

    def result(self) -> Any: ...

    def entity(self, entity_id: str) -> Any: ...

    def close(self) -> None: ...


class ThreadedRun:
    """A game state's run piloted on its own thread."""

    def __init__(self, branch: Branch, prefetch: Optional[Callable[[Any], Any]]):
        self._branch = branch
        self._pilot = branch._pilot
        self.prefetch = prefetch

    @property
    def pause(self) -> Optional[Pause]:
        return self._pilot.pause

    def read(self, fn: Callable[[Any], Any]) -> Any:
        env = self._pilot.env
        return self._pilot.read(lambda: fn(env))

    def read_prefetched(self) -> Any:
        return self.read(self.prefetch) if self.prefetch is not None else None

    def call(self, name: str, args: Any) -> Optional[ToolResult]:
        return self._pilot.call(name, args)

    def choose(self, index: int) -> Optional[ToolResult]:
        return self._pilot.choose(index)

    def clone(self) -> "ThreadedRun":
        return ThreadedRun(self._branch.clone(), self.prefetch)

    def result(self) -> Any:
        return self._branch.result()

    def entity(self, entity_id: str) -> Any:
        return self._branch.entity(entity_id)

    def close(self) -> None:
        self._branch.close()


def can_step(game: "Game") -> bool:
    """Whether the game's states can be stepped (see the module docs)."""
    root, contract, others = game._root, game.contract, game._others
    named = others is None or isinstance(others, str) or (
        isinstance(others, Mapping) and all(isinstance(value, str) for value in others.values()))
    stages_step = all(stage.turns != "scheduled" and not stage.atomic and not stage.valid and stage.time_limit is None
                      for stage in contract.stage_list())
    return (named and stages_step and hosts_for(root.world) is None and root.budget is None and root.time_limit is None
            and contract.physics is None and contract.space is None and not root.driver.turn_tool_specs())


def replayed(game: "Game", history: Sequence[Mapping[str, Any]]) -> ThreadedRun:
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
