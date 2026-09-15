"""Static checks for the state model: noise, per-entity dynamics."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, FrozenSet, List, Set

from .contract import EntityDynamics
from .entity_physics import MATH_NAMES
from .props import prop_type

if TYPE_CHECKING:
    from .check import _Checker

__all__ = ["check_physics_state"]


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
