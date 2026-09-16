"""``fg_env.gym``: one agent of a contract as a Gymnasium-style environment.

``reset(seed=...)`` starts an episode and returns ``(observation, info)``; ``step(action)`` makes one
tool call as the agent and returns ``(observation, reward, terminated, truncated, info)``. Every other
agent is played by participants. Gymnasium is not required; when it is installed the environment is a
``gymnasium.Env``.

* observation — ``{"text", "brief", "tools", "me", "round", "stage"}``: at the start of a turn ``text`` is
  the update the agent reads; while the turn goes on it is the last call's result.
* action — a tool call: ``{"tool": name, "args": {...}}``, ``(name, args)``, a tool name, or an action id
  (with ``action_ids=True``).
* reward — the contract's ``game.rewards`` for the agent, else the change in its ``game.returns``.
* terminated — the run is over; truncated — ``max_steps`` calls were made.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from .api import ContractLike, load
from .branch import Branch, copy_pilot
from .errors import ContractError, Issue, RunError
from .replay import Tape
from .returns import seat_ids, seat_returns, seat_rewards
from .runtime import Env
from .seeds import SeedTree, mint_seed
from .session import ToolResult

__all__ = ["GymEnv", "gym"]

try:  # optional: be a gymnasium.Env when gymnasium is installed
    import gymnasium as _gymnasium

    _Base: Any = _gymnasium.Env
except ImportError:  # pragma: no cover - exercised when gymnasium is absent
    _Base = object


class GymEnv(_Base):  # type: ignore[misc]
    """One agent of a contract as a Gymnasium-style environment. Create with :func:`fg_env.gym`."""

    metadata = {"render_modes": ["ansi"]}

    def __init__(self, root: Env, agent: str, *, others: Any, max_steps: Optional[int], action_ids: bool,
                 hosts: Any, render_mode: Optional[str]):
        contract = root.contract
        issues = []
        if agent not in root.world.entities or not contract.is_agent(root.world.entities[agent].entity_type):
            issues.append(Issue("agent", f"'{agent}' is not an agent of this contract",
                                f"agents: {', '.join(e.id for e in root.world.entities.values() if contract.is_agent(e.entity_type))}"))
        elif agent not in seat_ids(contract, root.world):
            issues.append(Issue("game.players", f"'{agent}' is not one of the game's seats", "add its type to game.players"))
        if contract.game is None or contract.game.returns is None:
            issues.append(Issue("game.returns", "is needed: rewards come from what the agent scores",
                                'e.g. "game": {"returns": "$actor.cash"}'))
        if issues:
            raise ContractError(issues, title="the contract cannot be a gym for this agent")
        self._root = root
        self.agent = agent
        self._others = others
        self._hosts = hosts
        self.max_steps = max_steps
        self.render_mode = render_mode
        self.action_space: Any = None
        self.observation_space: Any = None
        self._space = None
        if action_ids:
            from .game.space import ActionSpace

            self._space = ActionSpace(root)
        self._tree = SeedTree(root.seed)
        self._episode = 0
        self._branch: Optional[Branch] = None
        self._steps = 0
        self._return = 0.0
        self._turn_number: Optional[int] = None

    # -- the Gymnasium interface ---------------------------------------------------------------------

    def reset(self, *, seed: Optional[int] = None, options: Optional[Mapping[str, Any]] = None
              ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if seed is not None:
            self._tree, self._episode = SeedTree(seed), 0
        episode_seed = self._tree.derive("episode", self._episode)
        self._episode += 1
        self.close()
        root = self._root
        source = Env(root.contract, root.inputs, episode_seed, root.arm, parallel=1,
                     exposures=root.world.exposures is not None, assets=root.world.assets.catalog())
        source.origin.unarmed = root.origin.unarmed
        if self._hosts is not None:
            from .host.api import attach

            attach(source, self._hosts)
        pilot = copy_pilot(source, Tape(), 0, source.origin.base, controlled={self.agent}, explicit=False,
                           participants=self._others)
        pilot.start()
        self._branch = Branch(pilot)
        self._steps = 0
        self._return = self._agent_return()
        self._turn_number = None
        return self._observation(None), self._info(None)

    def step(self, action: Any) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]:
        branch = self._branch
        if branch is None:
            raise RuntimeError("call reset() before step()")
        if branch.pending is None:
            raise RuntimeError("the episode is over; call reset() to start another")
        tool, args = self._call(action)
        result = branch.call(tool, args)
        self._steps += 1
        if branch.finished and branch.result().status == "failed":
            raise RunError(f"the run failed: {branch.result().error}", "gym")
        now = self._agent_return()
        declared = branch._pilot.read(lambda: seat_rewards(self._root.contract, branch._pilot.env.world, [self.agent]))
        reward = declared[self.agent] if declared is not None else now - self._return
        self._return = now
        terminated = branch.pending is None
        truncated = not terminated and self.max_steps is not None and self._steps >= self.max_steps
        return self._observation(result), reward, terminated, truncated, self._info(result)

    def render(self) -> str:
        branch = self._branch
        if branch is None:
            return ""
        return branch.update if branch.pending is not None else branch.result().summary()

    def close(self) -> None:
        if self._branch is not None:
            self._branch.close()
            self._branch = None

    # -- internals -----------------------------------------------------------------------------------

    def _agent_return(self) -> float:
        branch = self._branch
        assert branch is not None
        env = branch._pilot.env
        return branch._pilot.read(lambda: seat_returns(self._root.contract, env.world, [self.agent]))[self.agent]

    def _call(self, action: Any) -> Tuple[str, Dict[str, Any]]:
        if isinstance(action, int) and not isinstance(action, bool):
            if self._space is None:
                raise ValueError("action ids need fg_env.gym(..., action_ids=True)")
            return self._space.decode(action)
        if isinstance(action, str):
            return action, {}
        if isinstance(action, Mapping):
            tool = action.get("tool", action.get("name"))
            args = action.get("args", action.get("input", action.get("arguments", {})))
            if isinstance(tool, str) and isinstance(args, Mapping):
                return tool, dict(args)
        if isinstance(action, (tuple, list)) and len(action) == 2 and isinstance(action[0], str) \
                and isinstance(action[1], Mapping):
            return action[0], dict(action[1])
        raise ValueError(f"an action is a tool call {{'tool': name, 'args': {{...}}}}, (name, args), a tool name or "
                         f"an action id, got {action!r}")

    def _observation(self, result: Optional[ToolResult]) -> Dict[str, Any]:
        branch = self._branch
        assert branch is not None
        me = branch.entity(self.agent)
        pending = branch.pending
        if pending is None:
            final = branch.result()
            return {"text": final.summary(), "brief": "", "tools": [], "me": me, "round": final.rounds, "stage": None}
        pause = branch._pilot.pause
        number = pause.wake._turn.number if pause is not None and pause.wake is not None else None
        fresh = result is None or number != self._turn_number
        self._turn_number = number
        text = branch.update if fresh or result is None else result.text
        tools: List[Dict[str, Any]] = [tool.to_dict() for tool in branch.tools]
        return {"text": text, "brief": branch.brief, "tools": tools, "me": me, "round": pending.round,
                "stage": pending.stage}

    def _info(self, result: Optional[ToolResult]) -> Dict[str, Any]:
        branch = self._branch
        assert branch is not None
        info: Dict[str, Any] = {"returns": branch.returns(), "steps": self._steps,
                                "result": None if result is None else {"ok": result.ok, "text": result.text,
                                                                       "data": dict(result.data)}}
        if self._space is not None and branch.pending is not None:
            from .game.space import legal_calls

            pilot = branch._pilot
            calls, _ = pilot.read(lambda: legal_calls(pilot.env, pilot.pause.wake._turn))  # type: ignore[union-attr]
            ids = sorted(i for i in (self._space.encode(tool, args) for tool, args in calls) if i is not None)
            info["legal_actions"] = ids
            info["action_mask"] = [1 if index in set(ids) else 0 for index in range(self._space.size)]
        return info


def gym(source: ContractLike, agent: str, *, others: Any = None, inputs: Optional[Mapping[str, Any]] = None,
        arm: Optional[str] = None, seed: Optional[int] = None, max_steps: Optional[int] = None,
        action_ids: bool = False, hosts: Any = None, render_mode: Optional[str] = None,
        data_dir: Union[str, "os.PathLike[str]", None] = None) -> GymEnv:
    """One agent (an entity id) of a contract as a Gymnasium-style environment; ``others`` play the rest.

    ``seed`` seeds the episodes (``reset(seed=...)`` reseeds them); ``max_steps`` truncates an episode after
    that many calls; ``action_ids=True`` accepts integer action ids and adds ``legal_actions`` and
    ``action_mask`` to ``info`` (see :func:`fg_env.game` for how ids are numbered).
    """
    if max_steps is not None and (isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1):
        raise ValueError(f"max_steps must be a whole number ≥ 1, got {max_steps!r}")
    root = load(source, inputs=inputs, seed=mint_seed() if seed is None else seed, arm=arm, data_dir=data_dir)
    return GymEnv(root, agent, others=others, max_steps=max_steps, action_ids=action_ids, hosts=hosts,
                  render_mode=render_mode)
