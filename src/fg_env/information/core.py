"""The run's Information component: everything an agent is shown or offered, rendered for that agent.

An agent reads its brief (rendered once and kept), its update, the views it looks at and the entities it inspects, and
is offered its tools; spectators read the spectator views, kept once a round as frames. :class:`Information` renders
all of it over the world — it changes nothing but the record of what was shown (the exposure log) — and draws what a
view's own randomness needs from a stream of the reading turn's own, so reading never moves the run's luck. Which
actions are legal is the rules' to decide: it receives them.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET, ActionBook
from ..contract import Contract, StageSpec, ViewSpec
from ..expr import shared_budget
from ..expr.objects import Entity
from ..expr.template import entity_handles
from ..expr.values import _Everyone
from ..world.parts import LogEvent
from ..world.store import World
from .exposure import ExposureLog, Shown, asks_seen
from .gate import render
from .perception import Perception, is_spectator
from .reads import find_target, handle_filter, inspect_rule, inspect_text, inspect_tool, look_tool, may_inspect
from .schemas import END_TURN, ToolSchemas, ToolSpec

if TYPE_CHECKING:
    from ..runtime.gate import Gate
    from ..runtime.state import RunState

__all__ = ["Information"]


class Information:
    """What agents and spectators read of the run whose world, action book and state these are (see the module
    docstring). ``like``: the part of the run this one's was copied from, whose reading of the contract it shares."""

    #: Whether some type lets agents inspect entities besides themselves (whose [id] handles then show).
    inspectable: bool
    #: The spectator views (`"for": "spectator"`), by name.
    spectator: list[str]

    def __init__(self, contract: Contract, world: World, actions: ActionBook, state: RunState,
                 gate: Gate, like: Information | None = None):
        self.contract = contract
        self.world = world
        self.state = state
        self.gate = gate
        self.perception: Perception = Perception(contract, world, like.perception if like is not None else None)
        self.schemas = ToolSchemas(actions)
        if like is not None:
            self.inspectable, self.spectator = like.inspectable, like.spectator
            return
        self.inspectable = any(inspect_rule(contract, kind) is not False for kind in contract.types)
        self.spectator = [name for name, view in contract.views.items() if is_spectator(view)]

    @staticmethod
    def exposure_log(contract: Contract, exposures: bool) -> ExposureLog | None:
        """What a run built from ``contract`` records of what its agents are shown: with ``exposures``, or when the
        contract's rules ask `$seen`."""
        return ExposureLog() if exposures or asks_seen(contract) else None

    # -- the gate ------------------------------------------------------------------------------------------------

    def render(self, template: str, vars: Mapping[str, Any], *, viewer: Entity | _Everyone | None,
               subject: str | None = None, path: str | None = None) -> str:
        """``template`` rendered for ``viewer`` over the run's world: the one gate (see information/gate.py)."""
        return render(self.world, template, vars, viewer=viewer, subject=subject, path=path)

    # -- brief and update ------------------------------------------------------------------------------------------

    def brief(self, actor: Entity) -> tuple[str, list[str]]:
        """``actor``'s brief and the assets it attaches: rendered on its first read, then kept for the run (a brief
        never changes, so providers can cache it)."""
        state = self.state
        brief = state.briefs.get(actor.id)
        if brief is None:
            attached: list[str] = []
            with shared_budget(ACTION_BUDGET, "brief"):
                brief = state.briefs[actor.id] = self.perception.brief(actor, attached)
            if attached:
                state.brief_assets[actor.id] = attached
        return brief, state.brief_assets.get(actor.id, [])

    def render_brief(self, actor: Entity) -> str:
        """``actor``'s brief as it renders now, not kept (for observers outside the run's turns)."""
        return self.perception.brief(actor)

    def update(self, actor: Entity, stage: StageSpec, reason: str, since: int, turn_no: int,
               time_limit: float | None = None, shown: Shown | None = None, attached: list[str] | None = None,
               calls: int | None = None, reads: bool = False, last: str | None = None) -> str:
        """``actor``'s update in its turn ``turn_no`` (see :meth:`Perception.update`), its [id] handles shown for
        the entities it may inspect and its views' luck drawn from the turn's own stream."""
        with shared_budget(ACTION_BUDGET, "update"), entity_handles(handle_filter(self, actor)), \
                self.world.luck.stream("update", turn_no):
            return self.perception.update(actor, stage, reason, since, time_limit, shown, attached, calls, reads,
                                          last)

    # -- views and news --------------------------------------------------------------------------------------------

    def applies(self, view: ViewSpec, actor: Entity) -> bool:
        """Whether ``view`` is for ``actor``'s type."""
        return self.perception.applies(view, actor)

    def look_views(self, actor: Entity) -> list[str]:
        """The views ``actor`` may look at."""
        return self.perception.look_views(actor)

    def render_view(self, name: str, view: ViewSpec, actor: Entity | None, shown: Shown | None = None,
                    attached: list[str] | None = None) -> str | None:
        """One view as text for ``actor`` (None: a spectator), or None when it shows nothing."""
        return self.perception.render_view(name, view, actor, shown, attached)

    def look(self, actor: Entity, name: str, turn_no: int, shown: Shown | None = None,
             attached: list[str] | None = None) -> str | None:
        """The view ``name`` as ``actor`` looks at it in its turn ``turn_no``: looking again shows the same noise
        (re-looking cannot average it away), and a preview of the turn shows what the turn will."""
        with shared_budget(ACTION_BUDGET, f"views.{name}"), entity_handles(handle_filter(self, actor)), \
                self.world.luck.stream("view", name, turn_no):
            return self.perception.render_view(name, self.contract.views[name], actor, shown, attached)

    def news(self, actor: Entity, since: int, limit: int | None = None, shown: Shown | None = None,
             attached: list[str] | None = None) -> tuple[list[str], int]:
        """News lines for ``actor`` after log position ``since``, and how many more were not shown (see
        :meth:`Perception.news`)."""
        return self.perception.news(actor, since, limit, shown, attached)

    def event_line(self, event: LogEvent, actor: Entity) -> str | None:
        """How ``event`` reads to ``actor`` as news, or None when it is not news to it."""
        return self.perception.event_line(event, actor)

    # -- inspect ---------------------------------------------------------------------------------------------------

    def may_inspect(self, viewer: Entity, target: Entity) -> bool:
        """Whether ``viewer`` may inspect ``target`` (itself always)."""
        return may_inspect(self, viewer, target)

    def inspect(self, viewer: Entity, wanted: Any) -> tuple[bool, str, list[str]]:
        """``viewer``'s inspect of the entity ``wanted`` names: whether one was found, what it shows (or the refusal,
        which suggests the closest id) and the ids of the files it references."""
        target, refusal = find_target(self, viewer, wanted)
        if target is None:
            return False, refusal, []
        text, files = inspect_text(self, viewer, target)
        return True, text, files

    # -- tools -----------------------------------------------------------------------------------------------------

    def tool(self, actor: Entity, name: str, staged: bool = False) -> ToolSpec:
        """The tool of action ``name`` as ``actor`` is offered it (a copy the caller may change)."""
        return self.schemas.tool(actor, name, staged)

    def tools(self, actor: Entity, legal: list[str], *, staged: bool, atomic: bool, allowance: int,
              must_act: bool) -> list[ToolSpec]:
        """The tools ``actor`` is offered: one per ``legal`` action, then look and inspect (``allowance`` free
        reads), then end_turn — left out while ``must_act`` holds and an action is offered. ``staged``: its choices
        are sealed until everyone has chosen; ``atomic``: its actions are checked together when the turn ends."""
        tools = self.schemas.tools(actor, legal, staged)
        looks = self.look_views(actor)
        if looks:
            tools.append(look_tool([(name, self.contract.views[name].title) for name in looks], allowance))
        inspect = inspect_tool(self, actor, allowance)
        if inspect is not None:
            tools.append(inspect)
        if not (must_act and any(tool.kind == "act" for tool in tools)):
            if staged:
                end_text = "Finish your turn (your choices are submitted)."
            elif atomic:
                end_text = "Finish your turn (your actions are checked together; a turn that is not allowed is undone)."
            else:
                end_text = "Finish your turn."
            tools.append(ToolSpec(END_TURN, end_text,
                                  {"type": "object", "properties": {}, "additionalProperties": False}, "end", True))
        return tools

    def offers_reads(self, actor: Entity, allowance: int) -> bool:
        """Whether ``actor`` is offered a read (a look view, or someone to inspect)."""
        return bool(self.look_views(actor)) or inspect_tool(self, actor, allowance) is not None

    # -- spectators ------------------------------------------------------------------------------------------------

    def spectate(self) -> dict[str, str]:
        """Every spectator view rendered against the world now, by name. Changes nothing: views that draw randomness
        use a stream of their own."""
        world, frames = self.world, self.state.frames
        shown: dict[str, str] = {}
        with self.gate, world.luck.turn_context(world.luck.seeds.rng("spectator", world.round, len(frames)), None):
            for name in self.spectator:
                with shared_budget(ACTION_BUDGET, f"views.{name}"):
                    text = self.perception.render_view(name, self.contract.views[name], None)
                if text is not None:
                    shown[name] = text
        return shown

    def frame(self, final: bool) -> None:
        """Keep a spectator frame of the world as it is now (one per round; the last one marked final)."""
        if not self.spectator:
            return
        world, frames = self.world, self.state.frames
        if frames and frames[-1]["round"] == world.round:
            frames.pop()  # the run ended at the start of a round: the frame it closed on is final
        frame: dict[str, Any] = {"round": world.round, "views": self.spectate()}
        if final:
            frame["final"] = True
        frames.append(frame)
