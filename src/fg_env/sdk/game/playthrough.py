"""Playthroughs: one game played through as reviewable text — what every seat reads at every decision, the
legal calls, the chosen step, chance outcomes and returns — so a change to rules or agent-facing wording shows
up as a diff of a golden file.

The steps are given (``steps``) or drawn at random from ``seed``. The text holds no timings and no digests of
anything but information states, so equal games give equal text on every machine.
"""
from __future__ import annotations

import json
import random
from typing import Any, List, Mapping, Optional, Sequence, Union

from ..api import ContractLike
from .game import Game, game
from .state import CHANCE, GameState
from .steps import Step, apply_step, random_step, step_text

__all__ = ["playthrough"]


def playthrough(source: Union[ContractLike, Game], *, seed: int = 0, steps: Optional[Sequence[Mapping[str, Any]]] = None,
                inputs: Optional[Mapping[str, Any]] = None, simultaneous: str = "joint", max_steps: int = 500) -> str:
    """The playthrough text of one game: ``steps`` when given (see :mod:`.steps`), else random ones from ``seed``."""
    subject = source if isinstance(source, Game) else game(source, inputs=inputs, seed=seed, simultaneous=simultaneous)
    rng = random.Random(f"fg-env-playthrough:{seed}")
    lines = _header(subject, seed)
    state = subject.new_initial_state()
    try:
        number = 0
        while True:
            lines += _describe(state, number)
            if state.is_terminal():
                break
            if number >= max_steps:
                lines.append(f"(stopped after {max_steps} steps)")
                break
            step = dict(steps[number]) if steps is not None and number < len(steps) else None
            if step is None and steps is not None:
                lines.append("(the given steps end here)")
                break
            chosen = step if step is not None else random_step(state, rng)
            lines.append(f"Chosen: {step_text(state, chosen)}")
            lines.append(f"Step: {json.dumps(chosen, sort_keys=True, default=str)}")
            lines.append("")
            apply_step(state, chosen)
            number += 1
    finally:
        state.close()
    return "\n".join(lines).rstrip() + "\n"


def _header(subject: Game, seed: int) -> List[str]:
    info = subject.info
    spec = subject.contract.game
    lines = [f"# fg-env playthrough: {subject.contract.name}",
             f"Game: {subject.id}",
             f"Seed: {seed}",
             f"Seats: {', '.join(f'{index} {entity}' for index, entity in enumerate(subject.players))}",
             f"Dynamics: {info['dynamics']}; chance: {info['chance_mode']}; information: {info['information']}; "
             f"utility: {subject.utility}",
             f"Action space: {subject.num_distinct_actions()} ids"
             + (f" (parametric: {', '.join(sorted(subject.space.parametric))})" if subject.space.parametric else "")]
    if spec is not None and (spec.min_return is not None or spec.max_return is not None):
        lines.append(f"Returns between: {_number(spec.min_return)} and {_number(spec.max_return)}")
    state = subject.new_initial_state()
    try:
        for index, entity in enumerate(subject.players):
            brief = state._run.read(lambda env: env.perception.brief(env.world.entities[entity]))  # noqa: B023 — called within this iteration
            lines += ["", f"## Brief of seat {index} ({entity})"] + [f"  | {line}" for line in str(brief).splitlines()]
    finally:
        state.close()
    return lines + [""]


def _describe(state: GameState, number: int) -> List[str]:
    lines = [f"# State {number}"]
    if state.is_terminal():
        lines.append("Terminal")
        lines += _returns(state)
        return lines
    if state.is_chance_node():
        outcomes = ", ".join(f"{state.action_to_string(CHANCE, outcome)} (p={_number(p)})"
                             for outcome, p in state.chance_outcomes())
        lines += ["Current player: chance", f"Chance outcomes: {outcomes}"]
    else:
        acting = state.acting_players()
        who = "simultaneous " + ", ".join(str(seat) for seat in acting) if state.is_simultaneous_node() else str(acting[0])
        lines.append(f"Current player: {who}")
        for seat in acting:
            listed = "; ".join(f"[{action.id}] {action.text}" for action in state.legal_tool_calls(seat))
            lines.append(f"Legal calls of seat {seat}: {listed or 'none'}")
            unlisted = state.unlisted_actions(seat)
            if unlisted:
                lines.append(f"Unlisted calls of seat {seat}: {', '.join(f'{k} ({v})' for k, v in unlisted.items())}")
    for seat, entity in enumerate(state.game.players):
        lines.append(f"Seat {seat} ({entity}) reads:")
        lines += [f"  | {line}" for line in state.observation_string(seat).splitlines()]
        lines.append(f"Seat {seat} information state: {state.information_state(seat)[:16]}")
    lines += _returns(state)
    return lines


def _returns(state: GameState) -> List[str]:
    spec = state.game.contract.game
    if spec is None or spec.returns is None:
        return []
    return [f"Returns: [{', '.join(_number(value) for value in state.returns())}]"]


def _number(value: Optional[float]) -> str:
    return "none" if value is None else f"{value:.6g}"


def steps_from_text(text: str) -> List[Step]:
    """The steps a playthrough chose, read back from its text (to replay it exactly)."""
    return [json.loads(line[len("Step: "):]) for line in text.splitlines() if line.startswith("Step: ")]
