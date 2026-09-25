"""Looking without changing the run: previews of an agent's next turn."""
from __future__ import annotations

import json
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, NoReturn

from ..actions.book import stage_actions
from ..contract import StageSpec
from ..errors import ContractError, Issue
from ..participants.builtin import policy_names
from ..runtime.turn import Turn
from ..runtime.turn_tools import HostWake

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["Preview", "Previews"]


class Preview(dict):
    """What an agent would receive on its next turn: ``preview.brief``, ``.update`` and ``.tools`` (as the agent reads
    them), ``.time_limit`` and rough ``.tokens`` per part — attributes, and the same as a mapping (JSON as it is).
    Printed, it reads as ``fg-env preview`` shows it. A record of a turn, not to be changed (``.update`` is the text
    the agent reads, not ``dict.update``)."""

    @property
    def brief(self) -> str:
        return str(self["brief"])

    @property
    def update(self) -> str:  # type: ignore[override]  # the text, not dict.update
        return str(self["update"])

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self["tools"])

    @property
    def time_limit(self) -> float | None:
        return self["time_limit"]  # type: ignore[no-any-return]

    @property
    def tokens(self) -> dict[str, int]:
        return dict(self["tokens"])

    def __str__(self) -> str:
        tools = "\n".join(f"- {tool['name']}: {tool['description']}  {json.dumps(tool['input_schema']['properties'])}"
                          for tool in self["tools"])
        tokens = self["tokens"]
        return (f"=== brief ===\n{self['brief']}\n\n=== update ===\n{self['update']}\n\n=== tools ===\n{tools}\n\n"
                f"~tokens: brief {tokens['brief']}, update {tokens['update']}, tools {tokens['tools']}")


class Previews:
    """Previews of an agent's next turn (and the probes they play on)."""

    def __init__(self, env: Env):
        self.env = env

    # -- preview -------------------------------------------------------------------------

    def preview(self, entity_id: str, stage: str | None, participants: Any = None) -> Preview:
        env = self.env
        if env.world.entity(entity_id) is None:
            agents = [e.id for e in env.world.entities.values() if env.contract.is_agent(e.entity_type)]
            _refuse("entity", f"no entity '{entity_id}'", entity_id, agents, "agents")
        stages = [s.name for s in env.contract.stage_list()]
        if stage is not None and stage not in stages:
            _refuse("stage", f"no stage '{stage}'", stage, stages, "stages")
        if env.finished or env.state.in_round:
            return self.now(entity_id, stage)
        probe = self.probe(participants)
        for point in probe.schedule.steps():
            if point.stage is not None and entity_id in point.reasons and stage in (None, point.stage.name):
                return probe.previews.turn(entity_id, point.stage, point.reasons[entity_id])
        start = self.probe(participants)  # not woken this round: show the round as it opens
        start.schedule.begin_round()
        return start.previews.now(entity_id, stage)

    def probe(self, participants: Any = None) -> Env:
        """A copy of the run to play a preview on (bound to the run's hosts), its agents played by ``participants`` —
        by default the run's built-in and named ones."""
        env = self.env
        probe: Env = env.copy()
        probe.parallel = 1
        policies = policy_names(env.contract)
        if participants is None:  # the run's own: only those that play for free
            probe.driver.spec = {k: v for k, v in env.driver.spec.items() if _plays_free(v, policies)}
        else:  # the caller's: its own callables play, but a named LLM or search algorithm never does
            probe.driver.bind(participants)
            probe.driver.spec = {k: v for k, v in probe.driver.spec.items()
                                 if callable(v) or _plays_free(v, policies)}
        probe.time_limit = env.time_limit
        return probe

    def now(self, entity_id: str, stage: str | None) -> Preview:
        """The turn as it would look in the current state, without playing anything."""
        env = self.env
        actor = env.world.entity(entity_id)
        assert actor is not None
        stages = env.contract.stage_list()
        acting = [s for s in stages if stage_actions(env.contract, s, actor.entity_type)]
        if stage is not None:
            spec = next(s for s in stages if s.name == stage)
        else:
            running = [s for s in acting if env.schedule.stage_runs(s)]
            spec = next((s for s in running if actor in env.schedule.eligible(s, ordered=False)), None) \
                or next(iter(running or acting or stages))
        reason = "Everyone chooses at the same time." if spec.turns == "simultaneous" else "It is your turn."
        if not env.schedule.stage_runs(spec):
            reason = f"(Preview only: stage {spec.name} does not run now.)"
        elif actor not in env.schedule.eligible(spec, ordered=False):
            reason = f"(Preview only: {actor.name} would not be woken in {spec.name} now.)"
        return self.turn(entity_id, spec, reason)

    def turn(self, entity_id: str, spec: StageSpec, reason: str) -> Preview:
        env = self.env
        actor = env.world.entities[entity_id]
        turn = Turn(env, actor, spec, reason, spec.turns == "simultaneous", peek=True)
        extras = env.driver.turn_tool_specs()
        if extras:  # what the agent will be offered, in-turn host tools included
            tools = HostWake(turn, extras).tools
        else:
            tools = turn.tools()
        return Preview(brief=turn.brief, update=turn.update, tools=[t.to_dict() for t in tools],
                       time_limit=turn.time_limit,
                       tokens={"brief": len(turn.brief) // 4, "update": len(turn.update) // 4,
                               "tools": len(json.dumps([t.to_anthropic() for t in tools])) // 4})


def _plays_free(participant: Any, policies: list[str]) -> bool:
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
