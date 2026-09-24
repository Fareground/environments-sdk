"""The world as a run left it, kept small for :meth:`RunResult.summary`: the author's look at state, so nobody has to
turn an output into a probe to see it."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..contract.base import TAPE
from ..world.values import plain_value
from .measure import shown

if TYPE_CHECKING:
    from ..contract import Contract
    from ..world.store import World

__all__ = ["end_state", "state_lines"]

#: Entities of each type a result keeps (the first ones, in creation order).
STATE_ROWS = 3
#: Values of each metric's series a summary shows (the last ones).
SERIES_TAIL = 5
#: Characters a summary line of metric values keeps.
LINE_WIDTH = 120
#: Characters of a text prop a summary shows.
TEXT_WIDTH = 24


def end_state(contract: Contract, world: World) -> dict[str, Any]:
    """``{world: {prop: value}, types: {type: {alive, entities: [{id, props}]}}}``: every world property and, per
    type with living entities, how many there are and the first :data:`STATE_ROWS` with every property, private ones
    included."""
    alive: dict[str, list[Any]] = {}
    for entity in world.entities.values():
        if entity.alive:
            alive.setdefault(entity.entity_type, []).append(entity)
    types = {kind: {"alive": len(alive[kind]),
                    "entities": [{"id": e.id, "props": plain_value(dict(e.properties))}
                                 for e in alive[kind][:STATE_ROWS]]}
             for kind in contract.types if kind in alive}
    return {"world": plain_value(dict(world.props)), "types": types}


def state_lines(state: dict[str, Any], series: dict[str, list[Any]]) -> list[str]:
    """The summary's last lines: each metric's recent values, then the end state."""
    lines: list[str] = []
    if series:
        lines.append(f"metrics (last {SERIES_TAIL} values):")
        lines += [_cut(f"  {name}: " + " → ".join(shown(v) for v in values[-SERIES_TAIL:]))
                  for name, values in series.items()]
    if not state:
        return lines
    lines.append("state at the end:")
    world = {name: value for name, value in state["world"].items() if name != TAPE}  # the engine's, not the author's
    if world:
        lines.append("  world: " + _props(world))
    for kind, group in state["types"].items():
        count = group["alive"]
        more = f", first {len(group['entities'])}" if count > len(group["entities"]) else ""
        lines.append(f"  {kind} ({count} alive{more}):")
        lines += [f"    {row['id']}: {_props(row['props'])}" for row in group["entities"]]
    return lines


def _props(props: dict[str, Any]) -> str:
    """Every prop as ``name=value, …``, long text cut short so the row stays readable."""
    return ", ".join(f"{name}={_cut(value, TEXT_WIDTH) if isinstance(value, str) else shown(value)}"
                     for name, value in props.items())


def _cut(text: str, width: int = LINE_WIDTH) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"
