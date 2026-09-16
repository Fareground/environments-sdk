"""Checking the world model: inputs, brief, clock and space, types and world properties, entities and
populations, links, physics and records."""
from __future__ import annotations

import datetime as _dt
import json
import keyword
import re
from pathlib import PurePath
from typing import TYPE_CHECKING, Iterable, Optional, Set

from ..physics import _CONSTS, _FUNCS, PhysicsExprError, _CompiledExpr
from . import contract as C
from .check_roots import BASE, ENTITY_FIELDS, ENTRY_FIELDS, RECORD_FIELD_TYPES
from .check_space import check_space
from .effects import POST_KEYS
from .expr import is_expr
from .inputs import DATA_SUFFIXES, check_value
from .world_defaults import default_order

if TYPE_CHECKING:
    from .check import _Checker
    from .check_roots import Types

__all__ = ["WorldChecks"]


class WorldChecks:
    """The world-model sections of a contract (mixed into the contract checker)."""

    def _inputs(self: "_Checker") -> None:  # type: ignore[misc]
        def visit(spec: C.InputSpec, path: str) -> None:
            if spec.type not in C.INPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{spec.type}'", self._suggest(spec.type, C.INPUT_TYPES))
                return
            if spec.type == "enum" and not spec.values:
                self.error(path, "an enum input needs `values`")
            if spec.type == "table":
                for column, kind in (spec.columns or {}).items():
                    if (kind not in C.INPUT_TYPES and kind != "asset") or kind in ("table",):
                        self.error(f"{path}.columns.{column}", f"unknown column type '{kind}'")
            if spec.source is not None:
                source = PurePath(spec.source)
                if source.is_absolute() or ".." in source.parts or not spec.source.strip():
                    self.error(f"{path}.source", f"'{spec.source}' must be a file name inside the data directory",
                               "use a relative path without '..'")
                elif source.suffix.lower() not in DATA_SUFFIXES:
                    self.error(f"{path}.source", f"'{spec.source}' is not a supported data file",
                               f"use one of: {', '.join(DATA_SUFFIXES)}")
                elif source.suffix.lower() == ".csv" and spec.type != "table":
                    self.error(f"{path}.source", f"a CSV file gives a table, but this input is {spec.type}", "set type: table")
                if spec.default is not None:
                    self.warn(f"{path}.default", "is never used: the data file provides the value",
                              "remove the default, or the source")
            if spec.default is not None:
                problem = check_value(spec.type, spec.default, spec)
                if problem:
                    self.error(f"{path}.default", problem)
            elif not spec.required and spec.source is None:
                self.warn(path, "has no default and is not required, so it may be null",
                          "give a default or set required: true")
            for field, child in (spec.fields or {}).items():
                visit(child, f"{path}.fields.{field}")
            if spec.items is not None:
                visit(spec.items, f"{path}.items")
        for name, spec in self.c.inputs.items():
            visit(spec, f"inputs.{name}")

    def _brief(self: "_Checker") -> None:  # type: ignore[misc]
        roots, types = BASE | {"actor"}, {"actor": set(self.agents)}
        self.template(self.c.brief.situation or None, "brief.situation", "actor", roots, types)
        self.template(self.c.brief.rules or None, "brief.rules", "actor", roots, types)
        for type_name, text in self.c.brief.roles.items():
            if self._type(type_name, f"brief.roles.{type_name}", agent=True):
                self.template(text, f"brief.roles.{type_name}", "actor", roots, {"actor": {type_name}})

    def _clock_space(self: "_Checker") -> None:  # type: ignore[misc]
        clock = self.c.clock
        if isinstance(clock.rounds, str):
            self.expr(clock.rounds, "clock.rounds", {"inputs"})
        elif clock.rounds < 1:
            self.error("clock.rounds", "must be at least 1")
        if clock.start and is_expr(clock.start):
            self.expr(clock.start, "clock.start", {"inputs"})
        elif clock.start:
            try:
                _dt.date.fromisoformat(clock.start[:10])
            except ValueError:
                self.error("clock.start", f"'{clock.start}' is not an ISO date", "e.g. 2026-01-31")
        if clock.step < 1:
            self.error("clock.step", "must be at least 1")
        if clock.mode not in ("rounds", "continuous"):
            self.error("clock.mode", f"unknown mode '{clock.mode}'", "rounds or continuous")
        elif clock.mode == "continuous":
            if clock.horizon is None and "rounds" not in clock.model_fields_set:
                self.error("clock", "a continuous clock needs a `horizon` (or an explicit `rounds` budget)",
                           "e.g. \"horizon\": 480 with unit minute")
            if isinstance(clock.horizon, str):
                self.expr(clock.horizon, "clock.horizon", {"inputs"})
        elif clock.horizon is not None or "tick" in clock.model_fields_set or "jump" in clock.model_fields_set:
            self.warn("clock", "horizon, tick and jump only apply with \"mode\": \"continuous\"")
        if clock.start and clock.unit.lower().rstrip("s") not in ("day", "week", "month", "year", "hour", "minute"):
            self.warn("clock.start", f"a calendar date is not shown for unit '{clock.unit}'",
                      "use day, week, month, year, hour or minute")
        if self.c.space is not None:
            check_space(self, self.c.space)

    def _prop_spec(self: "_Checker", spec: C.PropSpec, path: str, roots: Iterable[str], types: Optional[Types] = None) -> None:  # type: ignore[misc]
        if spec.type is not None and spec.type not in C.PROP_TYPES:
            self.error(f"{path}.type", f"unknown type '{spec.type}'", self._suggest(spec.type, C.PROP_TYPES))
        if spec.type == "enum" and not spec.values:
            self.error(path, "an enum property needs `values`")
        self.value(spec.default, f"{path}.default", roots, types or {})

    def _keyword_names(self: "_Checker") -> None:  # type: ignore[misc]
        """Names read as `$x.name` cannot be Python keywords (`$it.from`, `$params.in` do not parse)."""
        named = [(f"types.{t}.props.{p}", p) for t, spec in self.c.types.items() for p in spec.props]
        named += [(f"world.{p}", p) for p in self.c.world]
        named += [(f"inputs.{p}", p) for p in self.c.inputs]
        named += [(f"actions.{a}.params.{p}", p) for a, spec in self.c.actions.items() for p in spec.params]
        for path, name in named:
            if keyword.iskeyword(name):
                self.error(path, f"'{name}' is a reserved word, so expressions cannot read it", f"rename it, e.g. '{name}_'")

    def _types_and_world(self: "_Checker") -> None:  # type: ignore[misc]
        for name, spec in self.c.types.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                self.error(f"types.{name}", "type names are letters, digits and underscores")
            for prop, prop_spec in spec.props.items():
                if prop in ENTITY_FIELDS:
                    self.error(f"types.{name}.props.{prop}", f"'{prop}' is a built-in entity field", "choose another name")
                self._prop_spec(prop_spec, f"types.{name}.props.{prop}", BASE - {"metrics", "series"} | {"row", "i", "it"},
                                {"it": {name}})
            if spec.extends is not None:
                if spec.extends not in self.c.types:
                    self.error(f"types.{name}.extends", f"'{spec.extends}' is not a declared type",
                               self._suggest(spec.extends, self.c.types))
                elif name in self.c.lineage(spec.extends):
                    self.error(f"types.{name}.extends", "types extend each other in a cycle")
            if isinstance(spec.inspect, str):
                self.expr(spec.inspect, f"types.{name}.inspect", BASE | {"viewer", "it"},
                          {"viewer": set(self.agents), "it": set(self.c.subtypes(name))})
            if spec.policy is not None and spec.policy not in self.c.policies:
                self.error(f"types.{name}.policy", f"'{spec.policy}' is not a declared policy", self._suggest(spec.policy, self.c.policies))
            if self.c.is_agent(name) and not any(
                any(self.c.is_a(name, b) for b in ([a.by] if isinstance(a.by, str) else a.by)) for a in self.c.actions.values()
            ) and not any(self.c.is_a(other, name) and other != name for other in self.c.types):
                self.warn(f"types.{name}", "agent type has no actions", "add an action with `by`")
        for prop, world_spec in self.c.world.items():
            self._prop_spec(world_spec, f"world.{prop}", {"inputs", "world"})
        _, cycle = default_order({prop: spec.default for prop, spec in self.c.world.items()})
        if cycle is not None:
            self.error(f"world.{cycle[0]}.default", "world defaults read each other in a circle: "
                       + " → ".join(f"$world.{name}" for name in cycle),
                       "give one of them a literal default and set it in an opening event")

    def _entities(self: "_Checker") -> None:  # type: ignore[misc]
        for eid, spec in self.c.entities.items():
            path = f"entities.{eid}"
            if self._type(spec.type, f"{path}.type"):
                for prop, raw in spec.props.items():
                    if prop not in self.type_props[spec.type]:
                        self.error(f"{path}.props.{prop}", f"'{spec.type}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[spec.type]))
                    self.value(raw, f"{path}.props.{prop}", BASE)
            if spec.type in self.c.types:
                self.template(spec.brief, f"{path}.brief", "actor", BASE | {"actor"}, {"actor": {spec.type}})
        for index, group in enumerate(self.c.population):
            path = f"population[{index}]"
            if not self._type(group.type, f"{path}.type"):
                continue
            if group.count is None and group.from_ is None:
                self.error(path, "give `count`, `from`, or both")
            self.value(group.count, f"{path}.count", BASE)
            self.expr(group.from_, f"{path}.from", BASE)
            self.expr(group.where, f"{path}.where", BASE | {"row"})
            self.expr(group.weight, f"{path}.weight", BASE | {"row"})
            for key in ("id", "name"):
                self.template(getattr(group, key), f"{path}.{key}", None, BASE | {"row", "i"})
            self.template(group.brief, f"{path}.brief", "actor", BASE | {"row", "i", "actor"}, {"actor": {group.type}})
            it_types: Types = {"it": {group.type}}
            for prop, raw in group.props.items():
                if prop not in self.type_props[group.type]:
                    self.error(f"{path}.props.{prop}", f"'{group.type}' has no property '{prop}'",
                               self._suggest(prop, self.type_props[group.type]))
                self.value(raw, f"{path}.props.{prop}", BASE | {"row", "i", "it"}, it_types)
            names: Set[str] = set()
            for m_index, archetype in enumerate(group.mix):
                mpath = f"{path}.mix[{m_index}]"
                if archetype.name in names:
                    self.error(f"{mpath}.name", f"archetype '{archetype.name}' is declared twice")
                names.add(archetype.name)
                self.value(archetype.weight, f"{mpath}.weight", {"inputs"})
                for prop, raw in archetype.props.items():
                    if prop not in self.type_props[group.type]:
                        self.error(f"{mpath}.props.{prop}", f"'{group.type}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[group.type]))
                    self.value(raw, f"{mpath}.props.{prop}", BASE | {"row", "i", "it"}, it_types)
                self.template(archetype.brief, f"{mpath}.brief", "actor", BASE | {"row", "i", "actor"},
                              {"actor": {group.type}})
            if group.raking is not None and (group.from_ is None or group.count is None):
                self.error(f"{path}.raking", "raking reweights `from` rows for sampling; give `from` and `count`")
            for m_index, members in enumerate(group.members):
                mpath = f"{path}.members[{m_index}]"
                if not self._type(members.type, f"{mpath}.type"):
                    continue
                parent = {"parent": {group.type}, "it": {members.type}}
                self.value(members.count, f"{mpath}.count", BASE | {"parent", "row"}, parent)
                for prop, raw in members.props.items():
                    if prop not in self.type_props[members.type]:
                        self.error(f"{mpath}.props.{prop}", f"'{members.type}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[members.type]))
                    self.value(raw, f"{mpath}.props.{prop}", BASE | {"parent", "row", "i", "it"}, parent)
                if members.parent_prop is not None and members.parent_prop not in self.type_props[members.type]:
                    self.error(f"{mpath}.parent_prop", f"'{members.type}' has no property '{members.parent_prop}'")
                if members.link is not None and members.link not in self.c.relations:
                    self.error(f"{mpath}.link", f"'{members.link}' is not a declared relation",
                               self._suggest(members.link, self.c.relations))
                self.template(members.name, f"{mpath}.name", None, BASE | {"parent", "row", "i"}, parent)
                self.template(members.brief, f"{mpath}.brief", None, BASE | {"parent", "row", "i"}, parent)

    def _relations(self: "_Checker") -> None:  # type: ignore[misc]
        for index, link in enumerate(self.c.links):
            path = f"links[{index}]"
            if link.relation not in self.c.relations:
                self.error(f"{path}.relation", f"'{link.relation}' is not a declared relation",
                           self._suggest(link.relation, self.c.relations) or "declare it under `relations`")
            if not is_expr(link.value) and (isinstance(link.value, bool) or not isinstance(link.value, (int, float))):
                self.error(f"{path}.value", f"a link value must be a number, got {json.dumps(link.value, default=str)[:60]}",
                           "one number per link; declare other data as the relation's `props` and set them with `props`")
            graphs = ("complete", "ring", "random", "small_world", "scale_free", "blocks", "lattice", "star", "bipartite")
            if link.rows is not None:
                self.expr(link.rows, f"{path}.rows", BASE)
            elif link.among is not None:
                if self._type(link.among, f"{path}.among") and link.graph not in (None, *graphs):
                    self.error(f"{path}.graph", f"unknown graph '{link.graph}'", ", ".join(graphs))
                self.expr(link.where, f"{path}.where", BASE | {"it"}, {"it": {link.among}})
                self.value(link.degree, f"{path}.degree", BASE)
                self.value(link.p, f"{path}.p", BASE | {"from", "to"}, {"from": {link.among}, "to": {link.among}})
                self.value(link.m, f"{path}.m", BASE)
                self.value(link.p_between, f"{path}.p_between", BASE)
                self.expr(link.block, f"{path}.block", BASE | {"it"}, {"it": {link.among}})
                self.value(link.hub, f"{path}.hub", BASE)
                if link.graph == "blocks" and link.block is None:
                    self.error(path, "graph blocks needs `block` (an expression over $it giving each member's group)")
                if link.graph == "bipartite":
                    if link.with_ is None:
                        self.error(path, "graph bipartite needs `with` (the other type)")
                    else:
                        self._type(link.with_, f"{path}.with")
            elif link.from_ is None or link.to is None:
                self.error(path, "give `from` and `to`, or `among` with a `graph`")
            else:
                for key, raw in (("from", link.from_), ("to", link.to)):
                    if not is_expr(raw) and raw not in self.c.entities:
                        self.warn(f"{path}.{key}", f"'{raw}' is not a named entity", "use an id from `entities` or an expression")

    def _physics(self: "_Checker") -> None:  # type: ignore[misc]
        spec = self.c.physics
        if spec is None:
            return
        names = set(spec.vars) | set(spec.params) | set(spec.read) | set(_CONSTS) | set(_FUNCS) | {"t"}
        for name in spec.read:
            if name in spec.vars or name in spec.params:
                self.error(f"physics.read.{name}", f"'{name}' is also a variable or param, so the read would be ignored",
                           "give the read its own name and use it in the rates")
        for name, raw in spec.params.items():
            self.value(raw, f"physics.params.{name}", {"inputs", "world"})
        for name, src in spec.read.items():
            self.expr(src, f"physics.read.{name}", BASE - {"physics", "metrics", "series"})
        for name, var in spec.vars.items():
            self.value(var.start, f"physics.vars.{name}.start", {"inputs", "world"})
            if var.rate is not None:
                self._physics_expr(var.rate, f"physics.vars.{name}.rate", names)
        for target, src in spec.write.items():
            path = f"physics.write.{target}"
            owner, _, prop = target.partition(".")
            if owner == "world":
                if prop not in self.c.world:
                    self.error(path, f"world has no property '{prop}'", self._suggest(prop, self.c.world))
            elif owner in self.c.types:
                if prop not in self.type_props[owner]:
                    self.error(path, f"'{owner}' has no property '{prop}'", self._suggest(prop, self.type_props[owner]))
            else:
                self.error(path, "write targets are 'world.<prop>' or '<type>.<prop>'")
            self._physics_expr(src, path, names)

    def _physics_expr(self: "_Checker", source: str, path: str, names: Set[str]) -> None:  # type: ignore[misc]
        try:
            compiled = _CompiledExpr(source)
        except PhysicsExprError as exc:
            self.error(path, str(exc), "physics math uses bare names: beta*S*I/N")
            return
        unknown = compiled._names - names
        if unknown:
            self.error(path, f"unknown name(s) {sorted(unknown)}", "use physics variables, params or read names")

    def _records(self: "_Checker") -> None:  # type: ignore[misc]
        for name, spec in self.c.records.items():
            path = f"records.{name}"
            for field, kind in spec.fields.items():
                if kind not in RECORD_FIELD_TYPES:
                    self.error(f"{path}.fields.{field}", f"unknown field type '{kind}'", ", ".join(RECORD_FIELD_TYPES))
                if field in ENTRY_FIELDS:
                    self.error(f"{path}.fields.{field}", f"'{field}' is a built-in entry field")
                elif field in POST_KEYS:
                    self.error(f"{path}.fields.{field}", f"'{field}' is a `post` option, so a post cannot set it",
                               "rename the field")
            if spec.visible != "all":
                self.expr(spec.visible, f"{path}.visible", BASE | {"viewer", "it"}, {"viewer": set(self.agents)})
            self.template(spec.show, f"{path}.show", "it", BASE | {"actor", "it"}, {"actor": set(self.agents)})
