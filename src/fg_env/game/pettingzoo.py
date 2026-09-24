"""PettingZoo-style multi-agent environments over any game, with no dependency on PettingZoo.

* :func:`pettingzoo_aec` — the Agent Environment Cycle API: ``for agent in env.agent_iter(): obs, reward,
  termination, truncation, info = env.last(); env.step(action)``. ``reward`` is what the agent earned since it
  last acted (the change in its ``game.returns``), exactly PettingZoo's cumulative reward. Simultaneous stages
  are played one seat at a time, later seats not seeing earlier sealed choices.
* :func:`pettingzoo_parallel` — the parallel API: ``observations, rewards, terminations, truncations, infos =
  env.step({agent: action})``; agents that are not deciding at that moment have their actions ignored.

Observations are ``{"observation": the text the seat reads, "action_mask": 1 per legal action id}``; actions are
action ids (see :func:`fg_env.rl.game`) or tool calls. Chance is drawn from the environment's seed. A termination is
the end of the game; a truncation is ``max_steps`` decisions. When PettingZoo is installed the environments are
``pettingzoo.AECEnv`` / ``pettingzoo.ParallelEnv``; when Gymnasium is, ``action_space(agent)`` is ``Discrete`` and
``observation_space(agent)`` a ``Dict`` of ``Text`` and ``MultiBinary``.
"""
from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from typing import Any

from ..api import ContractLike
from .game import Game, game
from .state import GameState

__all__ = ["AECGame", "ParallelGame", "pettingzoo_aec", "pettingzoo_parallel"]

try:  # optional: be PettingZoo environments when PettingZoo is installed
    import pettingzoo as _pettingzoo

    _AECBase: Any = _pettingzoo.AECEnv
    _ParallelBase: Any = _pettingzoo.ParallelEnv
except ImportError:  # pragma: no cover - exercised when pettingzoo is absent
    _AECBase = object
    _ParallelBase = object

#: The longest observation text the Gymnasium observation space admits.
MAX_OBSERVATION_CHARS = 100_000


def _spaces(subject: Game) -> tuple[Any, Any]:
    try:
        from gymnasium import spaces
    except ImportError:
        return None, None
    import string

    charset = string.printable + "·×→←…—–’“”§•"
    observation = spaces.Dict({"observation": spaces.Text(MAX_OBSERVATION_CHARS, charset=charset),
                               "action_mask": spaces.MultiBinary(subject.num_distinct_actions())})
    return observation, spaces.Discrete(subject.num_distinct_actions())


class _Episode:
    """One game played for the adapters: chance drawn from the seed, returns tracked for rewards."""

    def __init__(self, subject: Game, rng: random.Random, max_steps: int | None):
        self.game = subject
        self.rng = rng
        self.max_steps = max_steps
        self.steps = 0
        self.state: GameState = subject.new_initial_state()
        self.returns = self._returns()
        self.settle()

    def _returns(self) -> list[float]:
        return self.state.returns()

    def settle(self) -> list[float]:
        """Resolve chance nodes; the change in every seat's return since the last settle."""
        while self.state.is_chance_node():
            outcomes, probabilities = zip(*self.state.chance_outcomes())
            self.state.apply_action(self.rng.choices(outcomes, probabilities)[0])
        now = self._returns()
        change = [after - before for after, before in zip(now, self.returns)]
        self.returns = now
        return change

    @property
    def over(self) -> bool:
        return self.state.is_terminal()

    @property
    def truncated(self) -> bool:
        return not self.over and self.max_steps is not None and self.steps >= self.max_steps

    def observation(self, seat: int) -> dict[str, Any]:
        mask = [0] * self.game.num_distinct_actions()
        if not self.over and seat in self.state.acting_players():
            for index in self.state.legal_actions(seat):
                mask[index] = 1
        return {"observation": self.state.observation_string(seat), "action_mask": mask}

    def close(self) -> None:
        self.state.close()


class AECGame(_AECBase):  # type: ignore[misc]
    """A game as a PettingZoo AEC environment (see the module docs). Create with :func:`pettingzoo_aec`."""

    metadata = {"render_modes": ["ansi"], "name": "fg_env_aec", "is_parallelizable": False}

    def __init__(self, subject: Game, *, seed: int | None, max_steps: int | None, render_mode: str | None):
        self.game = subject
        self.possible_agents: list[str] = list(subject.players)
        self.render_mode = render_mode
        self.max_steps = max_steps
        self._rng = random.Random(seed)
        self._observation_space, self._action_space = _spaces(subject)
        self._episode: _Episode | None = None
        self.agents: list[str] = []
        self.agent_selection = self.possible_agents[0]
        self.rewards: dict[str, float] = {}
        self._cumulative_rewards: dict[str, float] = {}
        self.terminations: dict[str, bool] = {}
        self.truncations: dict[str, bool] = {}
        self.infos: dict[str, dict[str, Any]] = {}

    def observation_space(self, agent: str) -> Any:
        return self._observation_space

    def action_space(self, agent: str) -> Any:
        return self._action_space

    def reset(self, seed: int | None = None, options: Mapping[str, Any] | None = None) -> None:
        if seed is not None:
            self._rng = random.Random(seed)
        self.close()
        self._episode = _Episode(self.game, self._rng, self.max_steps)
        self.agents = list(self.possible_agents)
        self.rewards = {agent: 0.0 for agent in self.agents}
        self._cumulative_rewards = {agent: 0.0 for agent in self.agents}
        self.terminations = {agent: False for agent in self.agents}
        self.truncations = {agent: False for agent in self.agents}
        self.infos = {agent: {} for agent in self.agents}
        self._after_step()

    def observe(self, agent: str) -> dict[str, Any]:
        return self._current().observation(self.game.seat(agent))

    def last(self, observe: bool = True) -> tuple[dict[str, Any] | None, float, bool, bool, dict[str, Any]]:
        agent = self.agent_selection
        return (self.observe(agent) if observe else None, self._cumulative_rewards[agent], self.terminations[agent],
                self.truncations[agent], self.infos[agent])

    def step(self, action: Any) -> None:
        agent = self.agent_selection
        if self.terminations[agent] or self.truncations[agent]:
            if action is not None:
                raise ValueError(f"agent {agent} is done: step(None) removes it")
            self._remove(agent)
            return
        episode = self._current()
        self._cumulative_rewards[agent] = 0.0
        episode.state.apply_action(action)
        episode.steps += 1
        self._after_step()

    def agent_iter(self, max_iter: int = 2 ** 63) -> Iterator[str]:
        for _ in range(max_iter):
            if not self.agents:
                return
            yield self.agent_selection

    def render(self) -> str:
        episode = self._episode
        if episode is None:
            return ""
        return str(episode.state) if episode.over else episode.state.observation_string(episode.state.current_player())

    def close(self) -> None:
        if self._episode is not None:
            self._episode.close()
            self._episode = None

    def _current(self) -> _Episode:
        if self._episode is None:
            raise RuntimeError("call reset() first")
        return self._episode

    def _after_step(self) -> None:
        episode = self._current()
        change = episode.settle()
        for seat, agent in enumerate(self.possible_agents):
            if agent in self.agents:
                self.rewards[agent] = change[seat]
                self._cumulative_rewards[agent] += change[seat]
        if episode.over or episode.truncated:
            for agent in self.agents:
                self.terminations[agent] = episode.over
                self.truncations[agent] = episode.truncated
            self.agent_selection = self.agents[0]
        else:
            self.agent_selection = self.possible_agents[episode.state.current_player()]

    def _remove(self, agent: str) -> None:
        self.agents.remove(agent)
        for table in (self.rewards, self._cumulative_rewards, self.terminations, self.truncations, self.infos):
            table.pop(agent, None)
        if self.agents:
            self.agent_selection = self.agents[0]


class ParallelGame(_ParallelBase):  # type: ignore[misc]
    """A game as a PettingZoo parallel environment (see the module docs). Create with :func:`pettingzoo_parallel`."""

    metadata = {"render_modes": ["ansi"], "name": "fg_env_parallel"}

    def __init__(self, subject: Game, *, seed: int | None, max_steps: int | None, render_mode: str | None):
        self.game = subject
        self.possible_agents: list[str] = list(subject.players)
        self.agents: list[str] = []
        self.render_mode = render_mode
        self.max_steps = max_steps
        self._rng = random.Random(seed)
        self._observation_space, self._action_space = _spaces(subject)
        self._episode: _Episode | None = None

    def observation_space(self, agent: str) -> Any:
        return self._observation_space

    def action_space(self, agent: str) -> Any:
        return self._action_space

    def reset(self, seed: int | None = None, options: Mapping[str, Any] | None = None
              ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if seed is not None:
            self._rng = random.Random(seed)
        self.close()
        self._episode = _Episode(self.game, self._rng, self.max_steps)
        self.agents = list(self.possible_agents)
        return self._observations(), {agent: {} for agent in self.agents}

    def step(self, actions: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, float], dict[str, bool],
                                                         dict[str, bool], dict[str, dict[str, Any]]]:
        episode = self._episode
        if episode is None or not self.agents:
            raise RuntimeError("call reset() first (the episode is over)")
        state = episode.state
        acting = state.acting_players()
        missing = [self.possible_agents[seat] for seat in acting if self.possible_agents[seat] not in actions]
        if missing:
            raise ValueError(f"agents {missing} decide now and need an action")
        if state.is_simultaneous_node():
            state.apply_actions({seat: actions[self.possible_agents[seat]] for seat in acting})
        else:
            state.apply_action(actions[self.possible_agents[acting[0]]])
        episode.steps += 1
        change = episode.settle()
        observations = self._observations()
        rewards = {agent: change[seat] for seat, agent in enumerate(self.possible_agents) if agent in self.agents}
        terminations = {agent: episode.over for agent in self.agents}
        truncations = {agent: episode.truncated for agent in self.agents}
        infos: dict[str, dict[str, Any]] = {agent: {} for agent in self.agents}
        if episode.over or episode.truncated:
            self.agents = []
        return observations, rewards, terminations, truncations, infos

    def render(self) -> str:
        episode = self._episode
        return "" if episode is None else str(episode.state)

    def close(self) -> None:
        if self._episode is not None:
            self._episode.close()
            self._episode = None

    def _observations(self) -> dict[str, dict[str, Any]]:
        episode = self._episode
        assert episode is not None
        return {agent: episode.observation(seat) for seat, agent in enumerate(self.possible_agents)
                if agent in self.agents}


def pettingzoo_aec(source: ContractLike, *, inputs: Mapping[str, Any] | None = None, seed: int | None = None,
                   max_steps: int | None = None, render_mode: str | None = None) -> AECGame:
    """A contract as a PettingZoo AEC environment (simultaneous stages one seat at a time; needs game.returns)."""
    return AECGame(game(source, inputs=inputs, seed=seed or 0, simultaneous="turn_based"), seed=seed,
                   max_steps=max_steps, render_mode=render_mode)


def pettingzoo_parallel(source: ContractLike, *, inputs: Mapping[str, Any] | None = None, seed: int | None = None,
                        max_steps: int | None = None, render_mode: str | None = None) -> ParallelGame:
    """A contract as a PettingZoo parallel environment (needs game.returns)."""
    return ParallelGame(game(source, inputs=inputs, seed=seed or 0), seed=seed, max_steps=max_steps,
                        render_mode=render_mode)
