"""How fast search code can walk a game: random playouts and clone-then-apply, in states per second.

Search algorithms spend their time in two operations — playing a state forward, and branching a
copy of it — so those are what is measured. Numbers depend on the machine; compare them on one
machine, before and after a change.
"""
from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional

from ..api import ContractLike
from .game import Game, game
from .state import GameState
from .steps import apply_step, random_step

__all__ = ["GameBench", "bench_game"]


@dataclass(frozen=True)
class GameBench:
    """What :func:`bench_game` measured."""

    game: str
    playouts: int
    states: int
    playout_seconds: float
    rollout_states: int
    rollout_seconds: float
    clones: int
    clone_seconds: float

    @property
    def states_per_second(self) -> float:
        return self.states / self.playout_seconds if self.playout_seconds > 0 else 0.0

    @property
    def playouts_per_second(self) -> float:
        return self.playouts / self.playout_seconds if self.playout_seconds > 0 else 0.0

    @property
    def rollout_states_per_second(self) -> float:
        return self.rollout_states / self.rollout_seconds if self.rollout_seconds > 0 else 0.0

    @property
    def clones_per_second(self) -> float:
        return self.clones / self.clone_seconds if self.clone_seconds > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "states_per_second": self.states_per_second,
                "playouts_per_second": self.playouts_per_second,
                "rollout_states_per_second": self.rollout_states_per_second, "clones_per_second": self.clones_per_second}

    def summary(self) -> str:
        return (f"{self.game}: {self.playouts_per_second:,.1f} random playouts/s "
                f"({self.states_per_second:,.0f} states/s; {self.rollout_states_per_second:,.0f} states/s sampling moves "
                f"without listing), {self.clones_per_second:,.1f} clone+apply/s")


def _random_move(state: GameState, rng: random.Random) -> None:
    apply_step(state, random_step(state, rng))


def _sampled_move(state: GameState, rng: random.Random) -> None:
    """A random move the way rollouts make one: without listing every legal call."""
    if state.is_chance_node() or state.is_simultaneous_node():
        _random_move(state, rng)
        return
    action = state.sample_legal_action(rng)
    if action is None:
        raise ValueError(f"seat {state.current_player()} must decide but has no listed legal call")
    state.apply_action(action)


def bench_game(source: ContractLike | Game, *, playouts: int = 50, clones: int = 200, seed: int = 0,
               inputs: Optional[Mapping[str, Any]] = None) -> GameBench:
    """Time ``playouts`` random playouts from the start (listing the legal calls at every state, then again sampling
    each move without listing, as rollouts do), then ``clones`` clone-and-apply steps from states met along random
    playouts (every state is branched once, one random move applied to the copy)."""
    if playouts < 1 or clones < 0:
        raise ValueError("playouts must be at least 1 and clones at least 0")
    subject = source if isinstance(source, Game) else game(source, inputs=inputs)
    rng = random.Random(seed)
    states = 0
    started = time.perf_counter()
    for _ in range(playouts):
        state = subject.new_initial_state()
        while not state.is_terminal():
            _random_move(state, rng)
            states += 1
        state.close()
    playout_seconds = time.perf_counter() - started
    rollout_states = 0
    started = time.perf_counter()
    for _ in range(playouts):
        state = subject.new_initial_state()
        while not state.is_terminal():
            _sampled_move(state, rng)
            rollout_states += 1
        state.close()
    rollout_seconds = time.perf_counter() - started
    done, clone_seconds = 0, 0.0
    while done < clones:
        state = subject.new_initial_state()
        while not state.is_terminal() and done < clones:
            started = time.perf_counter()
            child = state.clone()
            _random_move(child, rng)
            clone_seconds += time.perf_counter() - started
            child.close()
            done += 1
            _random_move(state, rng)
        state.close()
    return GameBench(subject.id, playouts, states, playout_seconds, rollout_states, rollout_seconds, clones, clone_seconds)
