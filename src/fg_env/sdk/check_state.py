"""Static checks for the state model: noise, per-entity dynamics, link fields."""
from __future__ import annotations

import keyword
import re
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set

from .contract import EntityDynamics, ParamSpec
from .entity_physics import MATH_NAMES
from .links import LINK_ATTRS
from .props import prop_type

if TYPE_CHECKING:
    from .check import Types, _Checker

__all__ = ["check_physics_state", "check_relation_fields", "check_link_fields"]

_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


def check_relation_fields(checker: "_Checker", base: FrozenSet[str]) -> None:
    """Declared link fields, and the fields each `links` entry sets."""
    every_type = set(checker.c.types)
    for kind, spec in checker.c.relations.items():
        for name, prop in spec.props.items():
            path = f"relations.{kind}.props.{name}"
            if name in LINK_ATTRS:
                checker.error(path, f"'{name}' is built into every link", "choose another field name")
            elif not _FIELD_NAME.match(name) or keyword.iskeyword(name):
                checker.error(path, f"'{name}' cannot be read as $link(...).{name}", "use letters, digits and _, not a reserved word")
            checker._prop_spec(prop, path, base - {"metrics", "series"} | {"from", "to"},
                               {"from": every_type, "to": every_type})
    for index, entry in enumerate(checker.c.links):
        roots = base | {"from", "to"} | ({"row"} if entry.rows is not None else set())
        check_link_fields(checker, entry.relation, entry.props, f"links[{index}].props", roots)


def check_link_fields(checker: "_Checker", relation: Any, fields: Any, path: str, roots: Iterable[str],
                      types: Optional["Types"] = None, params: Optional[Mapping[str, ParamSpec]] = None) -> None:
    """Fields set on a `relation` link: each declared, each value a valid expression here."""
    spec = checker.c.relations.get(relation) if isinstance(relation, str) else None
    if spec is None:
        return  # the unknown relation is reported where it is named
    if not isinstance(fields, dict):
        checker.error(path, "`props` is an object of link fields")
        return
    if fields and not spec.props:
        checker.error(path, f"relation '{relation}' declares no link fields", f"declare relations.{relation}.props")
        return
    for name, raw in fields.items():
        if name not in spec.props:
            checker.error(f"{path}.{name}", f"a {relation} link has no field '{name}'",
                          checker._suggest(name, spec.props) or f"fields: {', '.join(spec.props)}")
        checker.value(raw, f"{path}.{name}", roots, types, params)


def check_physics_state(checker: "_Checker", base: FrozenSet[str]) -> None:
    spec = checker.c.physics
    if spec is None:
        return
    world_names = set(spec.vars) | set(spec.params) | set(spec.read)
    for name, var in spec.vars.items():
        if var.noise is None:
            continue
        path = f"physics.vars.{name}.noise"
        if var.rate is None:
            checker.error(path, "noise needs a rate", "give the variable a rate (\"0\" for pure noise)")
        checker._physics_expr(var.noise, path, world_names | MATH_NAMES)
    for type_name, dynamics in spec.per.items():
        if checker._type(type_name, f"physics.per.{type_name}"):
            _entity_dynamics(checker, type_name, dynamics, world_names, base)
    for type_name, dynamics in spec.per.items():
        for other, other_dynamics in spec.per.items():
            if other == type_name or type_name not in checker.c.types or not checker.c.is_a(type_name, other):
                continue
            for shared in sorted(set(dynamics.vars) & set(other_dynamics.vars)):
                checker.error(f"physics.per.{type_name}.vars.{shared}", f"is also integrated by physics.per.{other}, "
                              f"which covers every {type_name}", f"integrate {shared} in one of the two")


def _entity_dynamics(checker: "_Checker", type_name: str, dynamics: EntityDynamics, world_names: Set[str],
                     base: FrozenSet[str]) -> None:
    path = f"physics.per.{type_name}"
    props = checker.c.props_of(type_name)
    numbers = {name for name, spec in props.items() if prop_type(spec) in ("number", "int")} - MATH_NAMES
    meanings: Dict[str, List[str]] = {}
    for label, names in (("world physics name", world_names), ("param", set(dynamics.params)),
                         ("read", set(dynamics.read)), (f"{type_name} property", numbers)):
        for name in names:
            meanings.setdefault(name, []).append(label)
    for name, labels in sorted(meanings.items()):
        if len(labels) > 1:
            checker.error(path, f"'{name}' is both a {labels[0]} and a {labels[1]}, so the math cannot tell them apart",
                          "rename one of them")
    for name in sorted((set(dynamics.params) | set(dynamics.read)) & MATH_NAMES):
        checker.error(path, f"'{name}' is a math function, constant or the time t", "choose another name")
    names = set(meanings) | MATH_NAMES
    for var, spec in dynamics.vars.items():
        where = f"{path}.vars.{var}"
        if var not in props:
            checker.error(where, f"'{type_name}' has no property '{var}'",
                          checker._suggest(var, props) or f"declare types.{type_name}.props.{var} as a number")
        elif prop_type(props[var]) != "number":
            checker.error(where, f"'{var}' is a {prop_type(props[var])} property; integrated variables are numbers",
                          "give the property a number default or \"type\": \"number\"")
        checker._physics_expr(spec.rate, f"{where}.rate", names)
        if spec.noise is not None:
            checker._physics_expr(spec.noise, f"{where}.noise", names)
    for name, raw in dynamics.params.items():
        checker.value(raw, f"{path}.params.{name}", {"inputs", "world"})
    entity_roots = base - {"metrics", "series"} | {"it"}
    entity_types = {"it": set(checker.c.subtypes(type_name))}
    for name, source in dynamics.read.items():
        checker.expr(source, f"{path}.read.{name}", entity_roots, entity_types)
    checker.expr(dynamics.where, f"{path}.where", entity_roots, entity_types)
    for prop, source in dynamics.write.items():
        where = f"{path}.write.{prop}"
        if prop not in props:
            checker.error(where, f"'{type_name}' has no property '{prop}'", checker._suggest(prop, props))
        elif prop in dynamics.vars:
            checker.error(where, f"'{prop}' is integrated, so a write would overwrite it", "write other properties")
        elif prop_type(props[prop]) not in ("number", "int", "bool"):
            checker.error(where, f"'{prop}' is a {prop_type(props[prop])} property; physics math gives numbers",
                          "write number, int or bool properties")
        checker._physics_expr(source, where, names)
