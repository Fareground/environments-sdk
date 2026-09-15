"""What an agent reads during a turn without acting: views by `look`, entities by `inspect`, and their [id] handles.

Reads do not spend the turn's tool calls: each turn has as many free reads as it has calls (the stage's `max_calls`),
so reading never uses up the calls an agent needs to act. Past that allowance a read uses a call like any other — the
backstop for a participant that only reads. The rule depends on nothing but the calls made, so runs stay deterministic.

`inspect` offers the ids it accepts (an enum when they are few, a compact listing otherwise), finds an entity by its
name as well as its id, and a refusal suggests the closest id. Entities an agent may inspect show their id next to
their name in what that agent reads (``Moderator [chair]``), so the handle to pass is always in view.
"""
from __future__ import annotations

import re
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..entity import Entity
from .actions import ToolSpec
from .errors import RunError
from .expr import ExprError, compile_expr, truthy

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["READS", "inspect_rule", "may_inspect", "inspectable", "look_tool", "inspect_tool", "find_target",
           "handle_filter", "compact_ids"]

#: The read tools.
READS = ("look", "inspect")
#: Ids an inspect schema offers as an enum up to this many (the entity parameters' limit); more are listed compactly.
_ENUM_IDS = 60
#: How close a mistyped id must be to a valid one to be suggested (difflib ratio).
_SUGGEST_CUTOFF = 0.6
_NUMBERED = re.compile(r"^(.*?)(\d+)$")


def inspect_rule(contract: Any, type_name: str) -> Any:
    """The inspect rule for a type, inherited through `extends`: true (default), false, or an expression."""
    for kind in reversed(contract.lineage(type_name)):
        if "inspect" in contract.types[kind].model_fields_set:
            return contract.types[kind].inspect
    return True


def may_inspect(env: "Env", viewer: Entity, target: Entity) -> bool:
    """Whether ``viewer`` may inspect ``target`` (itself always)."""
    rule = inspect_rule(env.contract, target.entity_type)
    if isinstance(rule, bool):
        return rule or target.id == viewer.id
    try:
        return target.id == viewer.id or truthy(compile_expr(rule)(env.world.scope(viewer=viewer, it=target)))
    except ExprError as exc:
        raise RunError(str(exc), f"types.{target.entity_type}.inspect") from None


def inspectable(env: "Env", viewer: Entity) -> List[Entity]:
    """The living entities ``viewer`` may inspect, in the world's order."""
    return [entity for entity in env.world.entities.values() if entity.alive and may_inspect(env, viewer, entity)]


def look_tool(looks: Sequence[str]) -> ToolSpec:
    return ToolSpec("look", "Show one of these views: " + ", ".join(looks) + ". Free: does not use a tool call.", {
        "type": "object", "properties": {"view": {"type": "string", "enum": list(looks)}},
        "required": ["view"], "additionalProperties": False}, "look")


def inspect_tool(env: "Env", viewer: Entity) -> ToolSpec:
    ids = [entity.id for entity in inspectable(env, viewer)]
    prop: Dict[str, Any] = {"type": "string"}
    description = "Details of one entity by its id (shown in [brackets] after names). Free: does not use a tool call."
    if len(ids) <= _ENUM_IDS:
        prop["enum"] = ids
    else:
        description += f" Ids: {compact_ids(ids)}."
    return ToolSpec("inspect", description, {"type": "object", "properties": {"id": prop}, "required": ["id"],
                                             "additionalProperties": False}, "look")


def find_target(env: "Env", viewer: Entity, wanted: Any) -> Tuple[Optional[Entity], str]:
    """The entity an inspect call names — by id, or by a name only one inspectable entity has — or a refusal that
    suggests the closest id. Only entities the viewer may inspect are ever found, named or suggested."""
    if isinstance(wanted, str):
        target = env.world.entity(wanted.strip())
        if target is not None and target.alive and may_inspect(env, viewer, target):
            return target, ""
    choices = inspectable(env, viewer)
    key = wanted.strip().lower() if isinstance(wanted, str) else ""
    named = [entity for entity in choices if key and (entity.name or "").lower() == key]
    if len(named) == 1:
        return named[0], ""
    text = "No entity with that id is available to inspect."  # the id is not repeated: it may name a hidden entity
    hint = _closest(key, choices) if key else None
    if hint is not None:
        return None, f"{text} Did you mean '{hint}'?"
    listed = compact_ids([entity.id for entity in choices])
    return None, f"{text} You can inspect: {listed or 'nothing'}."


def _closest(key: str, choices: Sequence[Entity]) -> Optional[str]:
    """The id whose own text or name is closest to ``key``."""
    by_text: Dict[str, str] = {}
    for entity in choices:
        by_text.setdefault(entity.id.lower(), entity.id)
        if entity.name:
            by_text.setdefault(entity.name.lower(), entity.id)
    found = get_close_matches(key, list(by_text), n=1, cutoff=_SUGGEST_CUTOFF)
    return by_text[found[0]] if found else None


def handle_filter(env: "Env", viewer: Entity) -> Optional[Callable[[Any], bool]]:
    """Which entities show their [id] handle in what ``viewer`` reads: those it may inspect (None: no inspect tool)."""
    if not env._inspectable:
        return None
    known: Dict[str, bool] = {}

    def show(entity: Any) -> bool:
        seen = known.get(entity.id)
        if seen is None:
            seen = known[entity.id] = entity.id != viewer.id and entity.alive and may_inspect(env, viewer, entity)
        return seen

    return show


def compact_ids(ids: Sequence[str]) -> str:
    """Ids as short text: runs of numbered ids become ranges (``u1–u150``); a very long listing is cut with a count."""
    parts: List[str] = []
    run: List[Tuple[str, int, str]] = []

    def flush() -> None:
        if len(run) > 2:
            parts.append(f"{run[0][2]}–{run[-1][2]}")
        else:
            parts.extend(item[2] for item in run)
        run.clear()

    for key in ids:
        match = _NUMBERED.match(key)
        if match is None or match.group(2).startswith("0") and match.group(2) != "0":
            flush()
            parts.append(key)
            continue
        prefix, number = match.group(1), int(match.group(2))
        if run and not (run[-1][0] == prefix and run[-1][1] + 1 == number):
            flush()
        run.append((prefix, number, key))
    flush()
    if len(parts) > _ENUM_IDS:
        return ", ".join(parts[:_ENUM_IDS]) + f" and {len(parts) - _ENUM_IDS} more"
    return ", ".join(parts)
