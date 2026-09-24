"""Links between entities: one number per link (``value``) plus the relation's typed fields.

The world keeps ``links[kind][(a, b)] = value``, ``link_fields[kind][(a, b)] = {field: value}`` and
an adjacency index; every change here is journaled, so an action that fails leaves every link and
field as it was. :class:`Link` is the live view expressions read as ``$link(a, b, kind)``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from .entity import Entity
from ..errors import RunError
from ..expr import ExprError, Untrusted, compile_expr, is_expr

if TYPE_CHECKING:
    from .live import SdkWorld

__all__ = ["Link", "LINK_ATTRS", "entity_id", "edge_key", "relation", "neighbors", "link", "unlink", "set_link_field",
           "link_view", "links_of", "rebuild_adjacency"]

Key = Tuple[str, str]

#: What every link exposes besides its relation's fields.
LINK_ATTRS = ("value", "source", "target", "kind")


class Link:
    """A live link: ``value``, ``source`` and ``target`` (entities), ``kind`` and the relation's fields.
    On a symmetric relation ``source`` is the end whose id sorts first."""

    __slots__ = ("world", "kind", "key")

    def __init__(self, world: "SdkWorld", kind: str, key: Key):
        self.world = world
        self.kind = kind
        self.key = key

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        edges = self.world.links[self.kind]
        if self.key not in edges:
            raise ExprError(f"the {self.kind} link {self.key[0]} → {self.key[1]} no longer exists", source)
        if name == "value":
            return edges[self.key]
        if name in ("source", "target"):
            return self.world.entities.get(self.key[0 if name == "source" else 1])
        if name == "kind":
            return self.kind
        fields = self.world.link_fields[self.kind].get(self.key, {})
        if name in fields:
            return fields[name]
        known = ", ".join([*LINK_ATTRS, *self.world.contract.relations[self.kind].props])
        raise ExprError(f"a {self.kind} link has no field '{name}' (fields: {known})", source)

    def as_dict(self) -> Dict[str, Any]:
        """Plain data: ids for the ends, the value and every field."""
        return {"source": self.key[0], "target": self.key[1], "kind": self.kind,
                "value": self.world.links[self.kind].get(self.key),
                **dict(self.world.link_fields[self.kind].get(self.key, {}))}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Link) and (other.world, other.kind, other.key) == (self.world, self.kind, self.key)

    def __hash__(self) -> int:
        return hash((self.kind, self.key))

    def __str__(self) -> str:
        names = [getattr(self.world.entities.get(end), "name", end) for end in self.key]
        return f"{names[0]} → {names[1]}"

    __repr__ = __str__


def entity_id(value: Any) -> str:
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, str):
        return value
    raise ExprError(f"expected an entity or id, got {value!r}")


def edges_of(world: "SdkWorld", kind: str, where: Optional[str] = None) -> Dict[Key, float]:
    if kind not in world.links:
        raise ExprError(f"'{kind}' is not a declared relation (relations: {', '.join(world.links) or 'none'})", where)
    return world.links[kind]


def edge_key(world: "SdkWorld", kind: str, a: str, b: str) -> Key:
    spec = world.contract.relations.get(kind)
    if spec is not None and spec.symmetric and b < a:
        return (b, a)
    return (a, b)


def relation(world: "SdkWorld", a: Any, b: Any, kind: str) -> Optional[float]:
    return edges_of(world, kind).get(edge_key(world, kind, entity_id(a), entity_id(b)))


def link_view(world: "SdkWorld", a: Any, b: Any, kind: str) -> Optional[Link]:
    key = edge_key(world, kind, entity_id(a), entity_id(b))
    return Link(world, kind, key) if key in edges_of(world, kind) else None


def neighbors(world: "SdkWorld", entity: Any, kind: str) -> List[Entity]:
    edges_of(world, kind)
    out: List[Entity] = []
    for other in world.adjacent[kind].get(entity_id(entity), {}):
        found = world.entities.get(other)
        if found is not None and found.alive:
            out.append(found)
    return out


def links_of(world: "SdkWorld", entity: Any, kind: str) -> List[Link]:
    """The ``kind`` links from ``entity`` (either direction on a symmetric relation) to living entities."""
    edges = edges_of(world, kind)
    me = entity_id(entity)
    out: List[Link] = []
    for other in world.adjacent[kind].get(me, {}):
        found = world.entities.get(other)
        key = edge_key(world, kind, me, other)
        if found is not None and found.alive and key in edges:
            out.append(Link(world, kind, key))
    if (me, me) in edges:
        out.append(Link(world, kind, (me, me)))
    return out


def link(world: "SdkWorld", kind: str, a: Any, b: Any, value: Any, where: str,
         fields: Optional[Mapping[str, Any]] = None) -> None:
    """Create or update a link. ``value`` None keeps an existing link's value (a new link gets the
    relation's default, or 1); ``fields`` set some of the relation's fields (a new link starts
    from their defaults)."""
    spec = world.contract.relations.get(kind)
    if spec is None:
        raise RunError(f"'{kind}' is not a declared relation (relations: {', '.join(world.contract.relations) or 'none'})", where)
    edges = world.links[kind]
    key = edge_key(world, kind, entity_id(a), entity_id(b))
    missing = key not in edges
    old = edges.get(key)
    if value is None:
        value = old if old is not None else (spec.default if spec.default is not None else 1)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(f"link value must be a number, got {value!r}", where)
    value = float(value)
    from .live import within_bounds

    within_bounds(spec, value, f"{_name(world, key[0])}'s {kind} link to {_name(world, key[1])}")
    table = world.link_fields[kind]
    had_fields = key in table
    old_fields = table.get(key)
    new_fields = _fields(world, kind, a, b, old_fields if not missing else None, fields or {}, where)
    edges[key] = value
    if missing:
        _adjust(world, kind, key, 1)
    if new_fields is not None:
        table[key] = new_fields

    def undo() -> None:
        if missing:
            edges.pop(key, None)
            _adjust(world, kind, key, -1)
        else:
            edges[key] = old  # type: ignore[assignment]
        if had_fields:
            table[key] = old_fields  # type: ignore[assignment]
        else:
            table.pop(key, None)

    world.journal.push(undo)


def _fields(world: "SdkWorld", kind: str, a: Any, b: Any, current: Optional[Dict[str, Any]],
            given: Mapping[str, Any], where: str) -> Optional[Dict[str, Any]]:
    """The link's fields after this change, or None when the relation declares none."""
    declared = world.contract.relations[kind].props
    if not declared:
        if given:
            raise RunError(f"relation '{kind}' declares no link fields", f"{where} → declare relations.{kind}.props")
        return None
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise RunError(f"a {kind} link has no fields {unknown} (fields: {', '.join(declared)})", where)
    from .live import _copy, _plain

    if current is not None:
        out = dict(current)
    else:
        out = {}
        scope = world.scope(**{"from": world.entity(entity_id(a)), "to": world.entity(entity_id(b))})
        for name, spec in declared.items():
            raw = spec.default
            try:
                start = compile_expr(raw)(scope) if is_expr(raw) and not isinstance(raw, Untrusted) else _copy(raw)
            except ExprError as exc:
                raise RunError(str(exc), f"relations.{kind}.props.{name}.default") from None
            out[name] = world._coerce(spec, _plain(start), f"relations.{kind}.props.{name}")
    for name, raw_value in given.items():
        out[name] = world._coerce(declared[name], _plain(raw_value), f"{where}.props.{name}")
    return out


def set_link_field(world: "SdkWorld", view: Link, name: str, value: Any, where: str) -> None:
    """Assign one field of an existing link (``value`` included), journaled."""
    kind, key = view.kind, view.key
    if key not in world.links[kind]:
        raise RunError(f"the {kind} link {key[0]} → {key[1]} no longer exists", where)
    if name == "value":
        link(world, kind, key[0], key[1], value, where)
        return
    if name in LINK_ATTRS:
        raise RunError(f"a link's `{name}` cannot be assigned", f"{where} → unlink and link again to change its ends")
    declared = world.contract.relations[kind].props
    if name not in declared:
        raise RunError(f"a {kind} link has no field '{name}' (fields: {', '.join([*LINK_ATTRS, *declared])})", where)
    fields = world.link_fields[kind][key]
    from .live import _plain

    new = world._coerce(declared[name], _plain(value), f"{where}.{name}")
    old = fields.get(name)
    fields[name] = new
    world.journal.push(lambda: fields.__setitem__(name, old))


def unlink(world: "SdkWorld", kind: str, a: Any, b: Any, where: str) -> None:
    edges = edges_of(world, kind, where)
    key = edge_key(world, kind, entity_id(a), entity_id(b))
    if key not in edges:
        return
    old = edges.pop(key)
    table = world.link_fields[kind]
    old_fields = table.pop(key, None)
    _adjust(world, kind, key, -1)

    def undo() -> None:
        edges[key] = old
        if old_fields is not None:
            table[key] = old_fields
        _adjust(world, kind, key, 1)

    world.journal.push(undo)


def _name(world: "SdkWorld", entity: str) -> str:
    found = world.entities.get(entity)
    return found.name if found is not None else entity


def _adjust(world: "SdkWorld", kind: str, key: Key, delta: int) -> None:
    a, b = key
    if a == b:
        return
    index = world.adjacent[kind]
    for x, y in ((a, b), (b, a)):
        row = index.setdefault(x, {})
        count = row.get(y, 0) + delta
        if count > 0:
            row[y] = count
        else:
            row.pop(y, None)


def rebuild_adjacency(world: "SdkWorld") -> None:
    world.adjacent = {kind: {} for kind in world.links}
    for kind, edges in world.links.items():
        for key in edges:
            _adjust(world, kind, key, 1)
