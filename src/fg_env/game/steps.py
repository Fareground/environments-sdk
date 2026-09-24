"""Decisions as data: one step of a game, chosen at random, applied, described and replayed.

A step is JSON-safe: ``{"chance": outcome}``, ``{"seat": s, "tool": name, "args": {...}}``, or a
simultaneous node's ``{"joint": {seat: {"tool": name, "args": {...}}}}``. A list of steps from the
initial state reproduces a playout exactly (the engine is deterministic under the game's seed), which
is how conformance issues, playthroughs and benchmarks name a position.
"""
from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .space import Action
from .state import CHANCE, GameState

if TYPE_CHECKING:
    from .game import Game

__all__ = ["Step", "random_step", "apply_step", "step_text", "replay_steps"]

Step = dict[str, Any]


def random_step(state: GameState, rng: random.Random) -> Step:
    """A random decision for ``state``: a chance outcome by its probability, or uniformly random listed calls.
    Raises ValueError when a seat that must decide has no listed call."""
    if state.is_chance_node():
        outcomes, probabilities = zip(*state.chance_outcomes())
        return {"chance": rng.choices(outcomes, probabilities)[0]}
    if state.is_simultaneous_node():
        return {"joint": {seat: _call(_pick(state, seat, rng)) for seat in state.acting_players()}}
    seat = state.current_player()
    return {"seat": seat, **_call(_pick(state, seat, rng))}


def _pick(state: GameState, seat: int, rng: random.Random) -> Action:
    legal = state.legal_tool_calls(seat)
    if not legal:
        unlisted = state.unlisted_actions(seat)
        detail = f" (its actions cannot be listed: {unlisted})" if unlisted else ""
        raise ValueError(f"seat {seat} ({state.game.players[seat]}) must decide but has no listed legal call{detail}")
    return rng.choice(legal)


def _call(action: Action) -> dict[str, Any]:
    return {"tool": action.tool, "args": dict(action.args)}


def apply_step(state: GameState, step: Mapping[str, Any]) -> None:
    """Apply one step. Raises ValueError when the step does not fit the node (another kind of node, another seat)."""
    if "chance" in step:
        if not state.is_chance_node():
            raise ValueError(f"step {dict(step)} is a chance outcome, but the state is not a chance node")
        state.apply_action(step["chance"])
    elif "joint" in step:
        if not state.is_simultaneous_node():
            raise ValueError(f"step {dict(step)} is a joint move, but the state is not a simultaneous node")
        state.apply_actions({int(seat): dict(call) for seat, call in step["joint"].items()})
    else:
        if state.current_player() != step["seat"]:
            raise ValueError(f"step {dict(step)} is seat {step['seat']}'s, but seat {state.current_player()} decides")
        state.apply_action({"tool": step["tool"], "args": dict(step["args"])})


def step_text(state: GameState, step: Mapping[str, Any]) -> str:
    """How a step reads, given the state it is applied to."""
    if "chance" in step:
        return state.action_to_string(CHANCE, step["chance"])
    if "joint" in step:
        return "; ".join(f"seat {int(seat)}: {Action(None, call['tool'], call['args']).text}"
                         for seat, call in sorted(step["joint"].items(), key=lambda item: int(item[0])))
    return f"seat {step['seat']}: {Action(None, step['tool'], step['args']).text}"


def replay_steps(game: Game, steps: Sequence[Mapping[str, Any]]) -> GameState:
    """The state reached by applying ``steps`` to a new initial state."""
    state = game.new_initial_state()
    try:
        for step in steps:
            apply_step(state, step)
    except BaseException:
        state.close()
        raise
    return state


def steps_to_json(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Steps with string seat keys in joint moves, as JSON writes them."""
    return [{"joint": {str(seat): dict(call) for seat, call in step["joint"].items()}} if "joint" in step
            else dict(step)
            for step in steps]
