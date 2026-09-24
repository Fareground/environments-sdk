"""Minimax with alpha-beta pruning for two-player zero-sum (or constant-sum) games of perfect information,
with expectation at chance nodes (expectiminimax) and a transposition table keyed by :meth:`GameState.state_key`.

Values are seat 0's return; seat 0 maximizes and seat 1 minimizes. With ``depth`` the search stops that many
decisions down and scores the frontier with ``evaluator`` (default: the mean of random rollouts).
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable

from ..space import Action
from ..state import GameState
from .mcts import RandomRolloutEvaluator

__all__ = ["minimax", "MinimaxBot", "check_perfect_information"]

_EXACT, _LOWER, _UPPER = 0, 1, 2

Evaluator = Callable[[GameState], float]


def check_perfect_information(state: GameState, algorithm: str) -> None:
    """Raise ValueError unless the game is two-player zero-sum or constant-sum with nothing hidden."""
    game = state.game
    if game.num_players() != 2:
        raise ValueError(f"{algorithm} needs a two-player game; this one has {game.num_players()} seats (use mcts)")
    if game.utility not in ("zero_sum", "constant_sum"):
        raise ValueError(f"{algorithm} needs a zero-sum or constant-sum game (game.utility is {game.utility}); use "
                         "mcts")
    if game.info["information"] == "imperfect":
        reasons = "; ".join(game.info["evidence"].get("information", [])[:2])
        raise ValueError(f"{algorithm} would see hidden information ({reasons}); use ismcts or cfr for this game")


def minimax(state: GameState, *, depth: int | None = None, evaluator: Evaluator | None = None,
            seed: int = 0, transpositions: bool = True) -> tuple[float, Action | None]:
    """``(value for seat 0, best call for the seat to move)`` of the state (the call is None at a terminal or
    chance node). Refuses games that are not two-player zero-sum with perfect information."""
    check_perfect_information(state, "minimax")
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int) or depth < 1):
        raise ValueError(f"depth must be a whole number ≥ 1, got {depth!r}")
    rng = random.Random(seed)
    rollout = RandomRolloutEvaluator()
    score = evaluator or (lambda frontier: rollout.evaluate(frontier, rng)[0])
    search = _Search(score, {} if transpositions else None)
    if state.is_terminal() or state.is_chance_node():
        return search.value(state, depth, -math.inf, math.inf), None
    if state.is_simultaneous_node():
        raise ValueError("minimax plays one seat at a time: search game.as_turn_based() for simultaneous stages")
    player = state.current_player()
    best_value, best_action = (-math.inf if player == 0 else math.inf), None
    for action in state.legal_tool_calls(player):
        child = state.child(action)
        try:
            value = search.value(child, None if depth is None else depth - 1, -math.inf, math.inf)
        finally:
            child.close()
        if best_action is None or (value > best_value if player == 0 else value < best_value):
            best_value, best_action = value, action
    return best_value, best_action


class _Search:
    def __init__(self, evaluator: Evaluator, table: dict[str, tuple[float, int, float]] | None):
        self.evaluator = evaluator
        self.table = table

    def value(self, state: GameState, depth: int | None, alpha: float, beta: float) -> float:
        if state.is_terminal():
            return state.returns()[0]
        if depth == 0:
            return self.evaluator(state)
        key = state.state_key() if self.table is not None else None
        remaining = math.inf if depth is None else float(depth)
        if key is not None and self.table is not None and key in self.table:
            stored, flag, stored_depth = self.table[key]
            if stored_depth >= remaining:
                if flag == _EXACT:
                    return stored
                if flag == _LOWER:
                    alpha = max(alpha, stored)
                elif flag == _UPPER:
                    beta = min(beta, stored)
                if alpha >= beta:
                    return stored
        start_alpha, start_beta = alpha, beta
        if state.is_chance_node():
            value = 0.0
            for outcome, probability in state.chance_outcomes():
                child = state.child(outcome)
                try:
                    value += probability * self.value(child, depth, -math.inf, math.inf)
                finally:
                    child.close()
        elif state.is_simultaneous_node():
            raise ValueError("minimax plays one seat at a time: search game.as_turn_based() for simultaneous stages")
        else:
            player = state.current_player()
            value = -math.inf if player == 0 else math.inf
            for action in state.legal_tool_calls(player):
                child = state.child(action)
                try:
                    result = self.value(child, None if depth is None else depth - 1, alpha, beta)
                finally:
                    child.close()
                if player == 0:
                    value = max(value, result)
                    alpha = max(alpha, value)
                else:
                    value = min(value, result)
                    beta = min(beta, value)
                if alpha >= beta:
                    break
        if key is not None and self.table is not None:
            flag = _EXACT if start_alpha < value < start_beta else (_LOWER if value >= start_beta else _UPPER)
            self.table[key] = (value, flag, remaining)
        return value


class MinimaxBot:
    """Plays the minimax move (see :func:`minimax`)."""

    def __init__(self, depth: int | None = None, *, evaluator: Evaluator | None = None, seed: int = 0):
        self.depth = depth
        self.evaluator = evaluator
        self.seed = seed

    def step(self, state: GameState) -> Action:
        _, action = minimax(state, depth=self.depth, evaluator=self.evaluator, seed=self.seed)
        if action is None:
            raise ValueError("there is no decision to make here (the state is terminal or a chance node)")
        return action
