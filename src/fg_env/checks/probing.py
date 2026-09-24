"""The probing play of :func:`fg_env.check`: an agent that tries to read hidden numbers out through its actions.

Binary-searching a hidden number takes a call whose refusal is free yet changes with the number. On each of its turns
the prober takes every numeric parameter of every action it may call, and every number hidden from it (a private
number of another entity, or of the world), puts the hidden number above the probe value and calls, then below it and
calls again. When a refusal that cost nothing answers differently, an agent could binary-search the number for free,
and check reports it. The engine spends every refusal whose rules read a hidden value (see runtime/turn.py), so this
finds only a read the engine could not see.
"""
from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING, Any

from ..errors import Issue
from ..participants.builtin import sample_args
from ..runtime.session import ToolResult

if TYPE_CHECKING:
    from ..contract import Contract
    from ..runtime.session import Wake

__all__ = ["Prober", "hides_numbers"]

#: The note a result ends with when the turn's calls run low (see runtime/turn.py).
_CALLS_NOTE = re.compile(r" \((?:Calls left: \d+|No tool calls left; your turn is over)\.\)$")


def hides_numbers(contract: Contract) -> bool:
    """Whether the contract declares a private property that may hold a number: the prober has something to find."""
    specs = [*contract.world.values(), *(spec for kind in contract.types for spec in contract.props_of(kind).values())]
    return any(spec.private and spec.type in (None, "number", "int") for spec in specs)


class Prober:
    """A participant that tries each numeric argument of its actions against each number hidden from it, and keeps an
    :class:`~fg_env.errors.Issue` for every one it could read out for free (see the module docstring)."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.found: dict[str, Issue] = {}

    def __call__(self, wake: Wake) -> None:
        env = wake._turn.env
        for tool in wake.tools:
            if tool.kind != "act" or tool.name not in env.contract.actions:
                continue
            args = sample_args(tool.input_schema, self.rng)
            for pname, prop in tool.input_schema["properties"].items():
                if prop.get("type") in ("integer", "number") and "minimum" in prop and "maximum" in prop:
                    self._probe(wake, tool.name, args, pname, prop)
        if not wake.done:
            wake.end()

    def _probe(self, wake: Wake, action: str, args: dict[str, Any], pname: str, prop: dict[str, Any]) -> None:
        low, high = prop["minimum"], prop["maximum"]
        middle = (low + high) // 2 if prop["type"] == "integer" else (low + high) / 2
        call = {**args, pname: middle}
        for owner, key, name, spec in _hidden_numbers(wake):
            sides = _around(spec, middle, max(high - low, 1))
            if sides is None:
                continue
            for first, second in (sides, sides[::-1]):  # the refusal may come on either side
                if wake.done or action in self.found:
                    return
                answer = self._answer(wake, action, call, owner, key, first)
                if answer is None or not _free(answer):
                    continue  # it acted or was spent: nothing learnt for free this way round
                other = self._answer(wake, action, call, owner, key, second)
                if other is not None and (other.ok or _said(other) != _said(answer)):
                    self.found[action] = Issue(
                        f"actions.{action}",
                        f"an agent can read {name} out for free: a refused {action} call costs it nothing, yet "
                        f"whether and how it is refused changes with that hidden number, so calling again and again "
                        f"with different {pname} binary-searches it (found by the probing agent)",
                        "make the refusal not depend on the hidden number, or decide it where reading it spends the "
                        "action (a `fail` in `do`)")
                break

    @staticmethod
    def _answer(wake: Wake, action: str, call: dict[str, Any], owner: Any, key: str, value: Any) -> ToolResult | None:
        """The answer to ``call`` while ``owner``'s (None: the world's) hidden ``key`` holds ``value``, which is then
        put back; None once the turn is over."""
        env = wake._turn.env
        with env.gate:  # no other turn meets the value put in for the probe
            if wake.done:
                return None
            store = env.world.props if owner is None else owner.properties
            kept = store[key]
            store[key] = value
            env.world.touch()  # answers remembered from the true value are stale
            try:
                return wake.call(action, call)
            finally:
                store[key] = kept
                env.world.touch()


def _around(spec: Any, middle: Any, span: Any) -> tuple[Any, Any] | None:
    """A value of the property ``spec`` above ``middle`` and one below it, within its own bounds; None when its bounds
    leave no room on one side."""
    above, below = middle + span, middle - span
    if spec.max is not None:
        above = min(above, spec.max)
    if spec.min is not None:
        below = max(below, spec.min)
    if spec.type == "int":
        above, below = int(above), int(below)
    return (above, below) if below < middle < above else None


def _said(result: ToolResult) -> str:
    """What a result says, without the note of the calls the turn has left (it changes with every call)."""
    return _CALLS_NOTE.sub("", result.text)


def _free(result: ToolResult) -> bool:
    return not result.ok and not result.data.get("spent") and result.data.get("error") in ("rejected", "invalid")


def _hidden_numbers(wake: Wake) -> list[tuple[Any, str, str, Any]]:
    """The numbers hidden from the waking agent: (the entity holding one, or None for the world; its property; how to
    name it; the property's spec)."""
    turn = wake._turn
    world, actor, contract = turn.env.world, turn.actor, turn.env.contract
    found: list[tuple[Any, str, str, Any]] = [
        (None, key, f"the world's hidden {key}", contract.world[key])
        for key in sorted(world.hidden.world) if _number(world.props.get(key))]
    for entity in world.entities.values():
        found += [(entity, key, f"{entity.name}'s hidden {key}", world.prop_spec(entity, key))
                  for key in sorted(world.hidden.types[entity.entity_type])
                  if entity.alive and world.hides(entity, key, actor) and _number(entity.properties.get(key))]
    return found


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
