"""What an agent reads during a turn without acting: views by `look`, entities by `inspect`, and their [id] handles.

Reads never spend the turn's tool calls. Each turn has as many free reads as it has calls (the stage's `max_calls`);
past that allowance a read is refused without spending anything, so an agent that still has an action open can always
take it. A participant that keeps reading after more refusals than the turn has calls has its turn ended — the backstop
for a loop that only reads. The same read twice in a turn answers that it is unchanged instead of repeating the text.
The rules depend on nothing but the calls made, so runs stay deterministic.

`inspect` offers the ids of entities with something to show (a property with a value, or a place), as an enum when
they are few and a compact listing otherwise; it finds an entity by its name as well as its id, and a refusal suggests
the closest id. Its result leaves out properties without a value. Entities an agent may inspect show their id next to
their name in what that agent reads (``Moderator [chair]``), so the handle to pass is always in view.
"""
from __future__ import annotations

from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..entity import Entity
from .actions import ToolSpec
from .assets.delivery import references
from .errors import RunError
from .expr import ExprError, compile_expr, truthy
from .template import format_value
from .tool_text import compact_ids, free_reads

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["READS", "UNCHANGED", "inspect_rule", "may_inspect", "inspectable", "look_tool", "inspect_tool",
           "inspect_text", "find_target", "handle_filter", "compact_ids", "reads_refused"]

#: The read tools.
READS = ("look", "inspect")
#: What a read repeated within a turn returns instead of the same text.
UNCHANGED = "Unchanged since you read it earlier this turn."
#: Ids an inspect schema offers as an enum up to this many (the entity parameters' limit); more are listed compactly.
_ENUM_IDS = 60
#: How close a mistyped id must be to a valid one to be suggested (difflib ratio).
_SUGGEST_CUTOFF = 0.6


def inspect_rule(contract: Any, type_name: str) -> Any:
    """The inspect rule for a type, inherited through `extends`: true (default), false, or an expression."""
    for kind in reversed(contract.lineage(type_name)):
        if "inspect" in contract.types[kind].model_fields_set:
            return contract.types[kind].inspect
    return True


def may_inspect(env: "Env", viewer: Entity, target: Entity) -> bool:
    """Whether ``viewer`` may inspect ``target`` (itself always)."""
    return _may_inspect_rule(env, viewer, target, inspect_rule(env.contract, target.entity_type))


def _may_inspect_rule(env: "Env", viewer: Entity, target: Entity, rule: Any) -> bool:
    if isinstance(rule, bool):
        return rule or target.id == viewer.id
    try:
        return target.id == viewer.id or truthy(compile_expr(rule)(env.world.scope(viewer=viewer, it=target)))
    except ExprError as exc:
        raise RunError(str(exc), f"types.{target.entity_type}.inspect") from None


def inspectable(env: "Env", viewer: Entity) -> List[Entity]:
    """The living entities ``viewer`` may inspect, in the world's order."""
    return [entity for entity in env.world.entities.values() if entity.alive and may_inspect(env, viewer, entity)]


def _offered(env: "Env", viewer: Entity) -> List[Entity]:
    """The inspectable entities worth offering: inspecting them shows more than their name."""
    # Type metadata is identical for every instance, but permissions and values
    # are live state: cache only metadata, and only for this listing.
    metadata: Dict[str, Tuple[Any, set[str]]] = {}
    offered: List[Entity] = []
    for entity in env.world.entities.values():
        if not entity.alive:
            continue
        kind = entity.entity_type
        if kind not in metadata:
            metadata[kind] = (inspect_rule(env.contract, kind),
                              {key for key, spec in env.contract.props_of(kind).items() if spec.private})
        rule, private = metadata[kind]
        if not _may_inspect_rule(env, viewer, entity, rule):
            continue
        own = entity.id == viewer.id
        if entity.location_id is not None or any(
                (own or key not in private) and not _empty(value) for key, value in entity.properties.items()):
            offered.append(entity)
    return offered


def _details(env: "Env", viewer: Entity, target: Entity) -> List[Tuple[str, Any]]:
    """The properties an inspect of ``target`` shows ``viewer``: its own private ones too, none without a value."""
    specs = env.contract.props_of(target.entity_type)
    own = target.id == viewer.id
    return [(key, value) for key, value in target.properties.items()
            if (own or not specs.get(key) or not specs[key].private) and not _empty(value)]


def _empty(value: Any) -> bool:
    return value is None or isinstance(value, (str, list, tuple, dict)) and len(value) == 0


def look_tool(looks: Sequence[Tuple[str, str]], allowance: int) -> ToolSpec:
    """The look tool over ``looks`` (view name, title)."""
    listed = ", ".join(f"{name} ({title})" if title else name for name, title in looks)
    description = (f"Show one of these views: {listed}. What happened since your last turn is already in your update. "
                   f"{free_reads(allowance)}")
    return ToolSpec("look", description, {
        "type": "object", "properties": {"view": {"type": "string", "enum": [name for name, _ in looks]}},
        "required": ["view"], "additionalProperties": False}, "look")


def inspect_tool(env: "Env", viewer: Entity, allowance: int) -> Optional[ToolSpec]:
    """The inspect tool, or None when nothing is worth inspecting."""
    ids = [entity.id for entity in _offered(env, viewer)]
    if not ids:
        return None
    prop: Dict[str, Any] = {"type": "string"}
    description = "Details of one entity by its id (shown in [brackets] after names)."
    if len(ids) <= _ENUM_IDS:
        prop["enum"] = ids
    else:
        description += f" Ids: {compact_ids(ids)}."
    return ToolSpec("inspect", f"{description} {free_reads(allowance)}", {
        "type": "object", "properties": {"id": prop}, "required": ["id"], "additionalProperties": False}, "look")


def inspect_text(env: "Env", viewer: Entity, target: Entity) -> Tuple[str, List[str]]:
    """What inspecting ``target`` shows ``viewer``, and the ids of the files it references."""
    specs = env.contract.props_of(target.entity_type)
    details = _details(env, viewer, target)
    files = [str(v) for k, v in details if specs.get(k) is not None and specs[k].type == "asset" and env.world.assets.has(v)]
    shown = [f"{k}: {references(env.world.assets, [v]) if v in files else format_value(v)}" for k, v in details]
    where = f" at {format_value(target.location_id)}" if target.location_id is not None else ""
    return f"{target.name} [{target.id}] ({target.entity_type}){where}" + ("\n" + "\n".join(shown) if shown else ""), files


def reads_refused(allowance: int, must_act: bool, stopped: bool) -> str:
    """The refusal of a read past the free allowance; ``stopped`` when the participant kept reading and its turn ends."""
    text = f"You have used your {allowance} free reads this turn; nothing was read."
    if stopped:
        return f"{text} You kept reading, so your turn is over."
    return f"{text} {'Take an action now.' if must_act else 'Act or end your turn.'}"


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
    listed = compact_ids([entity.id for entity in _offered(env, viewer)])
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
