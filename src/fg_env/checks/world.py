"""Checking the world model: inputs, brief, clock and space, types and world properties, named and generated
entities, links, physics and records."""
from __future__ import annotations

import datetime as _dt
import json
import re
from collections.abc import Iterable
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from .. import contract as C
from ..contract.inputs import DATA_SUFFIXES, check_value
from ..effects.runner import POST_KEYS
from ..expr import EXPRESSION_WORDS, ExprError, compile_expr, is_expr
from ..physics.model import _CONSTS, _FUNCS, CompiledExpr, PhysicsExprError
from ..world.defaults import default_order
from .core import Checker
from .roots import BASE, ENTITY_FIELDS, ENTRY_FIELDS, RECORD_FIELD_TYPES
from .space import check_space

if TYPE_CHECKING:
    from .roots import Types

__all__ = ["WorldChecks"]

#: A property default written as a number in quotes (`"profit": "0"`): text, where a number was almost always meant.
_NUMBER_TEXT = re.compile(r"-?\d+(\.\d+)?")


class WorldChecks(Checker):
    """The world-model sections of a contract (a part of the contract checker)."""

    def _inputs(self) -> None:
        def visit(spec: C.InputSpec, path: str) -> None:
            if spec.type not in C.INPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{spec.type}'", self._suggest_type(spec.type, C.INPUT_TYPES))
                return
            if spec.type == "enum" and not spec.values:
                self.error(path, "an enum input needs `values`")
            if spec.type == "table":
                for column, kind in (spec.columns or {}).items():
                    if (kind not in C.INPUT_TYPES and kind != "asset") or kind in ("table", "file"):
                        self.error(f"{path}.columns.{column}", f"unknown column type '{kind}'")
            if spec.source is not None:
                source = PurePath(spec.source)
                if source.is_absolute() or ".." in source.parts or not spec.source.strip():
                    self.error(f"{path}.source", f"'{spec.source}' must be a file name inside the data directory",
                               "use a relative path without '..'")
                elif spec.type == "file":
                    pass  # any file (or folder) the contract carries: its kind is checked when it is read
                elif source.suffix.lower() not in DATA_SUFFIXES:
                    self.error(f"{path}.source", f"'{spec.source}' is not a supported data file",
                               f"use one of: {', '.join(DATA_SUFFIXES)}")
                elif source.suffix.lower() == ".csv" and spec.type != "table":
                    self.error(f"{path}.source", f"a CSV file gives a table, but this input is {spec.type}",
                               "set type: table")
                if spec.default is not None:
                    self.warn(f"{path}.default", "is never used: the data file provides the value",
                              "remove the default, or the source")
            if spec.default is not None:
                problem = check_value(spec.type, spec.default, spec)
                if problem:
                    self.error(f"{path}.default", problem)
            elif not spec.required and spec.source is None and "default" not in spec.model_fields_set:
                self.warn(path, "has no default and is not required, so it may be null",
                          "give a default or set required: true")
            for field, child in (spec.fields or {}).items():
                visit(child, f"{path}.fields.{field}")
            if spec.items is not None:
                visit(spec.items, f"{path}.items")
        for name, spec in self.c.inputs.items():
            visit(spec, f"inputs.{name}")

    def _brief(self) -> None:
        roots, types = BASE | {"actor"}, {"actor": set(self.agents)}
        self.template(self.c.brief.situation or None, "brief.situation", "actor", roots, types)
        self.template(self.c.brief.rules or None, "brief.rules", "actor", roots, types)
        for type_name, text in self.c.brief.roles.items():
            if self._type(type_name, f"brief.roles.{type_name}", agent=True):
                self.template(text, f"brief.roles.{type_name}", "actor", roots, {"actor": {type_name}})

    def _clock_space(self) -> None:
        clock = self.c.clock
        self._count(clock.rounds, "clock.rounds")
        if clock.start and is_expr(clock.start):
            self.expr(clock.start, "clock.start", {"inputs"})
        elif clock.start:
            try:
                _dt.date.fromisoformat(clock.start[:10])
            except ValueError:
                self.error("clock.start", f"'{clock.start}' is not an ISO date", "e.g. 2026-01-31")
        if clock.step < 1:
            self.error("clock.step", "must be at least 1")
        if clock.start and clock.unit.lower().rstrip("s") not in ("day", "week", "month", "year", "hour", "minute"):
            self.warn("clock.start", f"a calendar date is not shown for unit '{clock.unit}'",
                      "use day, week, month, year, hour or minute")
        if self.c.space is not None:
            check_space(self, self.c.space)

    def _prop_spec(self, spec: C.PropSpec, path: str, roots: Iterable[str], types: Types | None = None) -> None:
        if isinstance(spec.private, list):
            agents = set(self.c.agent_types())
            if not spec.private:
                self.error(f"{path}.private", "lists no agent type that may read it",
                           "write true to hide it from all but its owner, or name the agent types that read it")
            for kind in spec.private:
                if kind not in agents:
                    self.error(f"{path}.private", f"'{kind}' is not an agent type",
                               self._suggest(kind, sorted(agents)) or f"agent types: {', '.join(sorted(agents))}")
        if spec.type is not None and spec.type not in C.PROP_TYPES:
            self.error(f"{path}.type", f"unknown type '{spec.type}'", self._suggest_type(spec.type, C.PROP_TYPES))
        if spec.type == "enum" and not spec.values:
            self.error(path, "an enum property needs `values`")
        if spec.type is None and isinstance(spec.default, str) and _NUMBER_TEXT.fullmatch(spec.default.strip()):
            self.warn(path, f"its default '{spec.default}' is text in quotes, so the property holds text, not a number",
                      f"write {spec.default.strip()} without quotes for a number, or declare "
                      f'{{"type": "text", "default": "{spec.default}"}} to keep text')
        self.value(spec.default, f"{path}.default", roots, types or {})
        literal = spec.default is not None and not (isinstance(spec.default, str) and is_expr(spec.default))
        if literal and spec.type in ("number", "int", "bool", "text", "list", "map"):
            problem = check_value(spec.type, spec.default)
            if problem:  # every entity would start with it: a static error here, not a smoke run's at each entity
                self.error(f"{path}.default", problem, f"give it a default of type {spec.type}")

    def _keyword_names(self) -> None:
        """Names expressions read cannot be the language's own words (`$count(in)`, `$it.not`)."""
        named = [(f"types.{t}", t) for t in self.c.types]
        named += [(f"entities.{e}", e) for e in self.c.entities]
        named += [(f"types.{t}.props.{p}", p) for t, spec in self.c.types.items() for p in spec.props]
        named += [(f"world.{p}", p) for p in self.c.world]
        named += [(f"inputs.{p}", p) for p in self.c.inputs]
        named += [(f"actions.{a}.params.{p}", p) for a, spec in self.c.actions.items() for p in spec.params]
        for path, name in named:
            if name in EXPRESSION_WORDS:
                self.error(path, f"'{name}' is a word expressions use themselves, so they cannot name it",
                           f"rename it, e.g. '{name}_'")

    def _types_and_world(self) -> None:
        for name, spec in self.c.types.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                self.error(f"types.{name}", "type names are letters, digits and underscores")
            for prop, prop_spec in spec.props.items():
                if prop in ENTITY_FIELDS:
                    fix = ("remove name from props; set name on the entity entry, outside props"
                           if prop == "name" else "choose another property name; built-in entity fields already exist")
                    self.error(f"types.{name}.props.{prop}", f"'{prop}' is a built-in entity field", fix)
                self._prop_spec(prop_spec, f"types.{name}.props.{prop}",
                                BASE - {"outputs", "series"} | {"row", "i", "it"}, {"it": {name}})
            if spec.extends is not None:
                if spec.extends not in self.c.types:
                    self.error(f"types.{name}.extends", f"'{spec.extends}' is not a declared type",
                               self._suggest(spec.extends, self.c.types))
                elif name in self.c.lineage(spec.extends):
                    self.error(f"types.{name}.extends", "types extend each other in a cycle")
            if spec.owner is not None:
                self._owner(name, spec.owner)
            if isinstance(spec.inspect, str):
                self.condition(spec.inspect, f"types.{name}.inspect", BASE | {"viewer", "it"},
                          {"viewer": set(self.agents), "it": set(self.c.subtypes(name))})
            if spec.policy is not None and spec.policy not in self.c.policies_of(name):
                self.error(f"types.{name}.policy", f"'{spec.policy}' is not a policy of {name}",
                           self._suggest(spec.policy, self.c.policies_of(name))
                           or f"declare it under types.{name}.policies")
            if self.c.is_agent(name) and not any(
                any(self.c.is_a(name, b) for b in ([a.by] if isinstance(a.by, str) else a.by))
                for a in self.c.actions.values()
            ) and not any(self.c.is_a(other, name) and other != name for other in self.c.types):
                self.warn(f"types.{name}", "agent type has no actions", "add an action with `by`")
        for prop, world_spec in self.c.world.items():
            self._prop_spec(world_spec, f"world.{prop}", {"inputs", "world"})
        _, cycle = default_order({prop: spec.default for prop, spec in self.c.world.items()})
        if cycle is not None:
            self.error(f"world.{cycle[0]}.default", "world defaults read each other in a circle: "
                       + " → ".join(f"$world.{name}" for name in cycle),
                       "give one of them a literal default and set it in an opening event")

    def _owner(self, kind: str, owner: str) -> None:
        """A type's `owner` names a public property of it: whose each entity is must be readable by the `where` that
        picks a reader's own."""
        props = self.c.props_of(kind)
        if owner not in props:
            self.error(f"types.{kind}.owner", f"'{owner}' is not a property of {kind}",
                       self._suggest(owner, props) or "name the property that holds the id of the agent each one "
                                                      "belongs to")
        elif props[owner].private:
            self.error(f"types.{kind}.owner", f"'{owner}' is private, so no agent could pick its own {kind} entities "
                                              "by it (`$it." + owner + " == $actor.id` reads it for every one)",
                       f"make {owner} public: a {kind}'s properties reach agents only through the views and tools that "
                       "show them")

    def _entities(self) -> None:
        self._generated_ids()
        for eid, spec in self.c.entities.items():
            path = f"entities.{eid}"
            if not self._type(spec.type, f"{path}.type"):
                continue
            generated = spec.generates
            roots = BASE | {"row", "i", "it"} if generated else BASE
            for prop, raw in spec.props.items():
                if prop not in self.type_props[spec.type]:
                    self.error(f"{path}.props.{prop}", f"'{spec.type}' has no property '{prop}'",
                               self._suggest(prop, self.type_props[spec.type]))
                self.value(raw, f"{path}.props.{prop}", roots, {"it": {spec.type}} if generated else None)
                if generated:
                    self._rebound_index(raw, f"{path}.props.{prop}")
            if not generated:
                self.template(spec.name, f"{path}.name", None, BASE)
                self.template(spec.brief, f"{path}.brief", "actor", BASE | {"actor"}, {"actor": {spec.type}})
                continue
            count = spec.count
            if isinstance(count, str) and not is_expr(count) or isinstance(count, bool) \
                    or isinstance(count, (int, float)) and (count < 0 or count != int(count)):
                self.error(f"{path}.count", f"must be a whole number ≥ 0 or an expression with $, got {count!r}",
                           'e.g. 12 or "$inputs.households"')
            else:
                self.value(count, f"{path}.count", BASE)
            self.expr(spec.from_, f"{path}.from", BASE)
            self.condition(spec.where, f"{path}.where", BASE | {"row"})
            self.expr(spec.weight, f"{path}.weight", BASE | {"row"})
            for key in ("id", "name"):
                self.template(getattr(spec, key), f"{path}.{key}", None, BASE | {"row", "i"})
            self.template(spec.brief, f"{path}.brief", "actor", BASE | {"row", "i", "actor"}, {"actor": {spec.type}})

    def _rebound_index(self, raw: Any, path: str) -> None:
        """`$i` in a generated entity's prop is the entity's number, but inside a function's per-item argument
        (`$dict(xs, $it, $random_for([$i, $it]))`) it is that function's item position: every entity gets the same."""
        try:
            functions = sorted({name for name, root in compile_expr(raw).item_roots if root == "i"}) \
                if isinstance(raw, str) and is_expr(raw) else []
        except ExprError:
            return  # reported by the expression check
        if functions:
            self.warn(path, f"`$i` inside ${functions[0]}(…) is that function's item position, not this entity's "
                            "number, so it is the same for every entity",
                      "give the entity its number in a prop of its own (\"n\": \"$i\") and read $outer.n inside the "
                      "function ($outer is the entity there)")

    def _generated_ids(self) -> None:
        """A generator with a literal count and default ids makes `<key>_<n>`: none may be a named entity's id."""
        named = list(self.c.named_entities())
        for key, spec in self.c.entities.items():
            if not spec.generates or spec.id is not None or spec.from_ is not None or not isinstance(spec.count, int):
                continue
            made = re.compile(re.escape(key) + r"_([1-9]\d*)")
            for eid in named:
                found = made.fullmatch(eid)
                if found and int(found.group(1)) <= spec.count:
                    self.error(f"entities.{key}", f"generates the id '{eid}', which entities.{eid} already has",
                               "rename one of them, or give the generator an `id` template")
                    break

    def _relations(self) -> None:
        for _, path, link in self.c.starting_links():
            if not is_expr(link.value) and (isinstance(link.value, bool) or not isinstance(link.value, (int, float))):
                self.error(f"{path}.value",
                           f"a link value must be a number, got {json.dumps(link.value, default=str)[:60]}",
                           "one number per link; declare other data as the relation's `props` and set them with "
                           "`props`")
            graphs = ("complete", "ring", "random", "small_world", "scale_free", "blocks", "lattice", "star",
                      "bipartite")
            if link.rows is not None:
                self.expr(link.rows, f"{path}.rows", BASE)
            elif link.among is not None:
                if self._type(link.among, f"{path}.among") and link.graph not in (None, *graphs):
                    self.error(f"{path}.graph", f"unknown graph '{link.graph}'", ", ".join(graphs))
                self.condition(link.where, f"{path}.where", BASE | {"it"}, {"it": {link.among}})
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
                    if not is_expr(raw) and raw not in self.c.named_entities():
                        self.warn(f"{path}.{key}", f"'{raw}' is not a named entity",
                                  "use an id from `entities` or an expression")

    def _physics(self) -> None:
        spec = self.c.physics
        if spec is None:
            return
        names = set(spec.vars) | set(spec.params) | set(spec.read) | set(_CONSTS) | set(_FUNCS) | {"t"}
        for name in spec.read:
            if name in spec.vars or name in spec.params:
                self.error(f"mechanisms.physics.read.{name}",
                           f"'{name}' is also a variable or param, so the read would be ignored",
                           "give the read its own name and use it in the rates")
        for name, raw in spec.params.items():
            self.value(raw, f"mechanisms.physics.params.{name}", {"inputs", "world"})
        for name, src in spec.read.items():
            self.expr(src, f"mechanisms.physics.read.{name}", BASE - {"physics", "outputs", "series"})
        for name, var in spec.vars.items():
            self.value(var.start, f"mechanisms.physics.vars.{name}.start", {"inputs", "world"})
            if var.rate is not None:
                self._physics_expr(var.rate, f"mechanisms.physics.vars.{name}.rate", names)
        for target, src in spec.write.items():
            path = f"mechanisms.physics.write.{target}"
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

    def _physics_expr(self, source: str, path: str, names: set[str]) -> None:
        try:
            compiled = CompiledExpr(source)
        except PhysicsExprError as exc:
            self.error(path, str(exc), "physics math uses bare names: beta*S*I/N")
            return
        unknown = compiled._names - names
        if unknown:
            self.error(path, f"unknown name(s) {sorted(unknown)}", "use physics variables, params or read names")

    def _records(self) -> None:
        for name, spec in self.c.records.items():
            path = f"records.{name}"
            for field, kind in spec.fields.items():
                if kind not in RECORD_FIELD_TYPES:
                    self.error(f"{path}.fields.{field}", f"unknown field type '{kind}'", ", ".join(RECORD_FIELD_TYPES))
                if field in ENTRY_FIELDS:
                    self.error(f"{path}.fields.{field}", f"'{field}' is a built-in entry field: every entry already "
                               f"has {', '.join(sorted(ENTRY_FIELDS))} (author: who posted it)",
                               f"rename the field (e.g. '{field}_name'), or read the built-in one as $it.{field}")
                elif field in POST_KEYS:
                    self.error(f"{path}.fields.{field}", f"'{field}' is a `post` option, so a post cannot set it",
                               "rename the field")
            if spec.visible != "all":
                self.condition(spec.visible, f"{path}.visible", BASE | {"viewer", "it"}, {"viewer": set(self.agents)},
                               fix='"all" shows every entry to everyone; otherwise write an expression over $viewer '
                                   'and $it, e.g. `$it.author == $viewer.id`')
            self.template(spec.show, f"{path}.show", "it", BASE | {"actor", "it"}, {"actor": set(self.agents)})
