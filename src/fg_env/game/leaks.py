"""The leak test: playouts that differ only in what a seat cannot see must look the same to that seat, and playouts
it was told apart must stay apart in its information state.

A playout is replayed with one step changed — another chance outcome, another call by the acting seat,
or another sealed choice by one seat of a simultaneous node — and both playouts continue with the
same later steps while those stay legal. At every pair of states where, for some seat that made the same calls in
both, the two worlds differ only in what the rules hide from it (other entities' private properties, events not
addressed to it, other seats' sealed choices), that seat's observation text, observation structure and
information state must be identical. Conversely (perfect recall), once a seat was told the playouts apart — its
observation text differed, or a call it made told it something different — its information states must differ at
every later pair: a solver built on them would otherwise solve a game in which the seat forgets. Terminal states are
left out: games reveal hidden cards at the end. Hidden information kept in world properties is not declared hidden, so
it is not tested.
"""
from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import RunError
from .observe import told, visible_key
from .state import GameState
from .steps import Step, apply_step, replay_steps

__all__ = ["Leak", "leak_issues"]


@dataclass(frozen=True)
class Leak:
    """Two playouts that one seat must not be able to tell apart, and what differs for it."""

    message: str
    steps: list[Step]
    other_steps: list[Step]


def leak_issues(game: Any, steps: Sequence[Step], rng: random.Random, branches: int) -> list[Leak]:
    """Leaks found by changing ``branches`` randomly chosen steps of the playout ``steps``."""
    found: list[Leak] = []
    hidden = [index for index, step in enumerate(steps) if "seat" not in step]
    public = [index for index, step in enumerate(steps) if "seat" in step]
    rng.shuffle(hidden)
    rng.shuffle(public)
    for index in (hidden + public)[:branches]:  # chance outcomes and sealed choices hide the most
        found.extend(_branch(game, list(steps), index, rng))
    return found


def _branch(game: Any, steps: list[Step], index: int, rng: random.Random) -> list[Leak]:
    here = replay_steps(game, steps[:index])
    try:
        alternatives = _alternatives(here, steps[index])
        if not alternatives:
            return []
        other_step = rng.choice(alternatives)
        other = here.clone()
        try:
            try:
                apply_step(other, other_step)
            except (ValueError, RunError):
                return []
            apply_step(here, steps[index])
            mine, theirs = steps[:index + 1], steps[:index] + [other_step]
            found: list[Leak] = []
            apart: set[int] = set()  # the seats told the two playouts apart so far
            position = index + 1
            while True:
                found.extend(_compare(here, other, mine, theirs, apart))
                if position >= len(steps) or here.is_terminal() or other.is_terminal() or _kind(here) != _kind(other):
                    return found
                try:
                    apply_step(other, steps[position])
                except (ValueError, RunError):
                    return found
                apply_step(here, steps[position])
                mine, theirs = mine + [steps[position]], theirs + [steps[position]]
                position += 1
        finally:
            other.close()
    finally:
        here.close()


def _kind(state: GameState) -> Any:
    return "chance" if state.is_chance_node() else ("joint" if state.is_simultaneous_node() else state.current_player())


def _alternatives(state: GameState, step: Mapping[str, Any]) -> list[Step]:
    if "chance" in step:
        return [{"chance": outcome} for outcome, _ in state.chance_outcomes() if outcome != step["chance"]]
    if "joint" in step:
        out: list[Step] = []
        for seat, call in step["joint"].items():
            for action in state.legal_tool_calls(int(seat)):
                if action.tool != call["tool"] or dict(action.args) != dict(call["args"]):
                    out.append({"joint": {**step["joint"], seat: {"tool": action.tool, "args": dict(action.args)}}})
        return out
    return [{"seat": step["seat"], "tool": action.tool, "args": dict(action.args)}
            for action in state.legal_tool_calls(step["seat"])
            if action.tool != step["tool"] or dict(action.args) != dict(step["args"])]


def _compare(a: GameState, b: GameState, steps: list[Step], other_steps: list[Step], apart: set[int]) -> list[Leak]:
    if a.is_terminal() or b.is_terminal():
        return []
    found: list[Leak] = []
    for seat, entity_id in enumerate(a.game.players):
        if seat not in apart and (a.observation_string(seat) != b.observation_string(seat)
                                  or _told(a, seat) != _told(b, seat)):
            apart.add(seat)
        if seat in apart and a.information_state_string(seat) == b.information_state_string(seat):
            changed = next((index for index, (s, t) in enumerate(zip(steps, other_steps)) if s != t), len(steps))
            found.append(Leak(f"seat {seat} ({entity_id}) was told two playouts apart (they differ at step "
                              f"{changed + 1}) but its information state is the same in both: it forgets what it was "
                              "told, so a solver treats the two as one → report this as an SDK bug", steps,
                              other_steps))
        if _own_calls(steps, seat) != _own_calls(other_steps, seat) or _visible(a, seat) != _visible(b, seat):
            continue  # a seat tells playouts apart by its own calls (a refused one leaves no trace in the world)
        difference = _difference(a, b, seat)
        if difference is not None:
            changed = next((index for index, (s, t) in enumerate(zip(steps, other_steps)) if s != t), len(steps))
            found.append(Leak(f"seat {seat} ({entity_id}) can tell apart two states that differ only in what it cannot "
                              f"see (the playouts differ at step {changed + 1}): {difference} → show hidden "
                              "information through an event or message when the rules reveal it, never by reading "
                              "another entity's private property or a sealed choice in a view", steps, other_steps))
    return found


def _own_calls(steps: Sequence[Step], seat: int) -> list[Any]:
    """The calls ``seat`` made in ``steps``: its own, which it knows whatever they did."""
    calls: list[Any] = []
    for step in steps:
        if step.get("seat") == seat:
            calls.append((step["tool"], step["args"]))
        elif "joint" in step:
            calls.append(next((call for key, call in step["joint"].items() if int(key) == seat), None))
    return calls


def _visible(state: GameState, seat: int) -> str:
    actor_id = state.game.players[seat]
    return str(state._run.read(lambda env: visible_key(env, env.world.entities[actor_id],
                                                       _own_pending(state._pending(env), actor_id))))


def _told(state: GameState, seat: int) -> list[str]:
    actor_id = state.game.players[seat]
    return list(state._run.read(lambda env: told(env, env.world.entities[actor_id])))


def _own_pending(pending: dict[str, Any], actor_id: str) -> dict[str, Any]:
    """The pending decision as ``actor_id`` may know it: another seat's turn shows only whose it is and where."""
    sealed = {key: value for key, value in (pending.get("sealed") or {}).items() if key == actor_id}
    if "actor" in pending and pending["actor"] != actor_id:
        return {"actor": pending["actor"], "stage": pending["stage"], "sealed": sealed}
    return {**pending, "sealed": sealed} if "sealed" in pending else pending


def _difference(a: GameState, b: GameState, seat: int) -> str | None:
    text_a, text_b = a.observation_string(seat), b.observation_string(seat)
    if text_a != text_b:
        return f"its observation text differs ({_first_difference(text_a, text_b)})"
    struct_a, struct_b = a.observation(seat, "struct"), b.observation(seat, "struct")
    if struct_a != struct_b:
        keys = sorted(key for key in set(struct_a) | set(struct_b) if struct_a.get(key) != struct_b.get(key))
        return f"its observation structure differs in {', '.join(keys)}"
    info_a, info_b = a.information_state_string(seat), b.information_state_string(seat)
    if info_a != info_b:
        return f"its information state differs ({_first_difference(info_a, info_b)})"
    return None


def _first_difference(a: str, b: str) -> str:
    for line_a, line_b in zip(a.splitlines(), b.splitlines()):
        if line_a != line_b:
            return f"{line_a!r} vs {line_b!r}"
    return f"{len(a.splitlines())} vs {len(b.splitlines())} lines"
