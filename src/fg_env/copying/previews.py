"""Looking without changing the run: previews of an agent's next turn, and spectator views."""
from __future__ import annotations

import json
from collections.abc import Mapping
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, NoReturn

from ..actions.book import ACTION_BUDGET, stage_actions
from ..contract import StageSpec
from ..errors import ContractError, Issue
from ..expr import shared_budget
from ..runtime.perception import is_spectator
from ..runtime.turn import Turn
from ..runtime.turn_tools import HostWake
from .snapshot import restore_env

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["Previews"]


class Previews:
    """Previews of an agent's next turn (and the probes they play on); spectator views and frames."""

    def __init__(self, env: Env):
        self.env = env
        self.spectator = [name for name, view in env.contract.views.items() if is_spectator(view)]
        #: Spectator views rendered at the end of every round, the last one marked final.
        self.frames: list[dict[str, Any]] = []

    # -- spectator ---------------------------------------------------------------------

    def spectate(self) -> dict[str, str]:
        env, world = self.env, self.env.world
        shown: dict[str, str] = {}
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
        frame: dict[str, Any] = {"round": world.round, "views": self.spectate()}
        if final:
            frame["final"] = True
        self.frames.append(frame)

    # -- preview -------------------------------------------------------------------------

    def preview(self, entity_id: str, stage: str | None, participants: Any = None) -> dict[str, Any]:
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
        probe = self.probe(snapshot, participants)
        for point in probe._round():
            if point.stage is not None and entity_id in point.reasons and stage in (None, point.stage.name):
                return probe.previews.turn(entity_id, point.stage, point.reasons[entity_id])
        start = self.probe(snapshot, participants)  # not woken this round: show the round as it opens
        start._begin_round()
        return start.previews.now(entity_id, stage)

    def probe(self, snapshot: Mapping[str, Any], participants: Any = None) -> Env:
        """A restored copy of the run to play a preview on (hosts bind their copies here), its agents played by
        ``participants`` — by default the run's built-in and named ones."""
        env = self.env
        probe = restore_env(type(env), env.contract, snapshot, parallel=1)
        policies = env.contract.policies
        if participants is None:  # the run's own: only those that play for free
            probe.driver.spec = {k: v for k, v in env.driver.spec.items() if _plays_free(v, policies)}
        else:  # the caller's: its own callables play, but a named LLM or search algorithm never does
            probe.driver.bind(participants)
            probe.driver.spec = {k: v for k, v in probe.driver.spec.items()
                                 if callable(v) or _plays_free(v, policies)}
        probe.time_limit = env.time_limit
        return probe

    def now(self, entity_id: str, stage: str | None) -> dict[str, Any]:
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

    def turn(self, entity_id: str, spec: StageSpec, reason: str) -> dict[str, Any]:
        env = self.env
        actor = env.world.entities[entity_id]
        turn = Turn(env, actor, spec, reason, spec.turns == "simultaneous", peek=True)
        extras = env.driver.turn_tool_specs()
        if extras:  # what the agent will be offered, in-turn host tools included
            tools = HostWake(turn, extras).tools
        else:
            tools = turn.tools()
        return {"brief": turn.brief, "update": turn.update, "tools": [t.to_dict() for t in tools],
                "time_limit": turn.time_limit,
                "tokens": {"brief": len(turn.brief) // 4, "update": len(turn.update) // 4,
                           "tools": len(json.dumps([t.to_anthropic() for t in tools])) // 4}}


def _plays_free(participant: Any, policies: Mapping[str, Any]) -> bool:
    """Whether a participant plays the earlier turns of a preview: only the built-in ones that cost nothing and answer
    at once (random, idle, a contract policy). An LLM, a search algorithm or your own callable is replaced by the
    agent's default (its type's policy, else random): a preview never makes a paid or slow call."""
    if not isinstance(participant, str):
        return False
    return participant in ("random", "idle") or participant.removeprefix("policy:") in policies


def _refuse(path: str, message: str, name: str, known: list[str], kind: str) -> NoReturn:
    hint = get_close_matches(name, known, n=1)
    raise ContractError([Issue(path, message, (f"did you mean '{hint[0]}'? " if hint else "")
                               + f"{kind}: {', '.join(known[:20]) or 'none'}")], title="cannot preview")
