"""Looking without changing the run: previews of an agent's next turn, and spectator views."""
from __future__ import annotations

import json
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, NoReturn, Optional

from .actions import ACTION_BUDGET, stage_actions
from .contract import StageSpec
from .errors import ContractError, Issue
from .expr import shared_budget
from .perception import is_spectator
from .snapshot import restore_env
from .turn import Turn

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["Previews"]


class Previews:
    """Previews of an agent's next turn (and the probes they play on); spectator views and frames."""

    def __init__(self, env: "Env"):
        self.env = env
        self.spectator = [name for name, view in env.contract.views.items() if is_spectator(view)]
        #: Spectator views rendered at the end of every round, the last one marked final.
        self.frames: List[Dict[str, Any]] = []

    # -- spectator ---------------------------------------------------------------------

    def spectate(self) -> Dict[str, str]:
        env, world = self.env, self.env.world
        shown: Dict[str, str] = {}
        with env._lock, world.turn_context(env.seeds.rng("spectator", world.round, len(self.frames)), None):
            for name in self.spectator:
                with shared_budget(ACTION_BUDGET, f"views.{name}"):
                    text = env.perception.render_view(name, env.contract.views[name], None)
                if text is not None:
                    shown[name] = text
        return shown

    def frame(self, final: bool) -> None:
        """Keep a spectator frame of the world as it is now (one per round; the last one marked final)."""
        if not self.spectator:
            return
        world = self.env.world
        if self.frames and self.frames[-1]["round"] == world.round:
            self.frames.pop()  # the run ended at the start of a round: the frame it closed on is final
        frame: Dict[str, Any] = {"round": world.round, "views": self.spectate()}
        if world.continuous:
            frame["time"] = world.time
        if final:
            frame["final"] = True
        self.frames.append(frame)

    # -- preview -------------------------------------------------------------------------

    def preview(self, entity_id: str, stage: Optional[str]) -> Dict[str, Any]:
        env = self.env
        if env.world.entity(entity_id) is None:
            agents = [e.id for e in env.world.entities.values() if env.contract.is_agent(e.entity_type)]
            _refuse("entity", f"no entity '{entity_id}'", entity_id, agents, "agents")
        stages = [s.name for s in env.contract.stage_list()]
        if stage is not None and stage not in stages:
            _refuse("stage", f"no stage '{stage}'", stage, stages, "stages")
        if env.finished or env._in_round:
            return self.now(entity_id, stage)
        snapshot = env.snapshot()
        probe = self.probe(snapshot)
        for point in probe._round():
            if point.stage is not None and entity_id in point.reasons and stage in (None, point.stage.name):
                return probe.previews.turn(entity_id, point.stage, point.reasons[entity_id])
        start = self.probe(snapshot)  # not woken this round: show the round as it opens
        start._begin_round()
        return start.previews.now(entity_id, stage)

    def probe(self, snapshot: Mapping[str, Any]) -> "Env":
        """A restored copy of the run to play a preview on (hosts bind their copies here)."""
        env = self.env
        probe = restore_env(type(env), env.contract, snapshot, parallel=1)
        probe.driver.spec = {k: v for k, v in env.driver.spec.items() if isinstance(v, str)}
        probe.time_limit = env.time_limit
        return probe

    def now(self, entity_id: str, stage: Optional[str]) -> Dict[str, Any]:
        """The turn as it would look in the current state, without playing anything."""
        env = self.env
        actor = env.world.entity(entity_id)
        assert actor is not None
        stages = env.contract.stage_list()
        acting = [s for s in stages if stage_actions(env.contract, s, actor.entity_type)]
        if stage is not None:
            spec = next(s for s in stages if s.name == stage)
        else:
            running = [s for s in acting if env._stage_runs(s)]
            spec = next((s for s in running if actor in env._eligible(s, ordered=False)), None) \
                or next(iter(running or acting or stages))
        reason = "Everyone chooses at the same time." if spec.turns == "simultaneous" else "It is your turn."
        if not env._stage_runs(spec):
            reason = f"(Preview only: stage {spec.name} does not run now.)"
        elif actor not in env._eligible(spec, ordered=False):
            reason = f"(Preview only: {actor.name} would not be woken in {spec.name} now.)"
        return self.turn(entity_id, spec, reason)

    def turn(self, entity_id: str, spec: StageSpec, reason: str) -> Dict[str, Any]:
        env = self.env
        actor = env.world.entities[entity_id]
        turn = Turn(env, actor, spec, reason, spec.turns == "simultaneous", peek=True)
        extras = env.driver.turn_tool_specs()
        if extras:  # what the agent will be offered, in-turn host tools included
            from .host.turn_tools import HostWake

            tools = HostWake(turn, extras).tools
        else:
            tools = turn.tools()
        return {"brief": turn.brief, "update": turn.update, "tools": [t.to_dict() for t in tools],
                "time_limit": turn.time_limit,
                "tokens": {"brief": len(turn.brief) // 4, "update": len(turn.update) // 4,
                           "tools": len(json.dumps([t.to_anthropic() for t in tools])) // 4}}


def _refuse(path: str, message: str, name: str, known: List[str], kind: str) -> NoReturn:
    hint = get_close_matches(name, known, n=1)
    raise ContractError([Issue(path, message, (f"did you mean '{hint[0]}'? " if hint else "")
                               + f"{kind}: {', '.join(known[:20]) or 'none'}")], title="cannot preview")
