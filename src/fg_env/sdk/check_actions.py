"""Checking actions and their parameters, stages, and views."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Mapping, Set

from . import contract as C
from .check_params import check_param_bounds
from .probability import check_literal_probability
from .check_roots import BASE
from .check_turns import check_spectator_view, check_stage_turns, spectator_audience_issues
from .contract import Contract
from .expr import ExprError
from .perception import SPECTATOR
from .template import compile_template

if TYPE_CHECKING:
    from .check import _Checker
    from .check_roots import Types

__all__ = ["ActionChecks"]


class ActionChecks:
    """The action, stage and view sections of a contract (mixed into the contract checker)."""

    def _actions(self: "_Checker") -> None:  # type: ignore[misc]
        for name, spec in self.c.actions.items():
            path = f"actions.{name}"
            by = [spec.by] if isinstance(spec.by, str) else spec.by
            by_types = {t for t in by if self._type(t, f"{path}.by", agent=True)}
            types: Types = {"actor": by_types}
            if spec.tool is not None:
                self._tool_group(spec, path)
            for pname, param in spec.params.items():
                ppath = f"{path}.params.{pname}"
                if param.type not in C.PARAM_TYPES:
                    self.error(f"{ppath}.type", f"unknown type '{param.type}'", self._suggest(param.type, C.PARAM_TYPES))
                    continue
                if param.type == "entity":
                    if param.of is None:
                        self.error(ppath, "an entity parameter needs `of` (the entity type)")
                    elif self._type(param.of, f"{ppath}.of"):
                        self.expr(param.where, f"{ppath}.where", BASE | {"actor", "it", "i", "params"},
                                  {"actor": by_types, "it": {param.of}}, spec.params)
                elif param.type == "list":
                    self._list_param(param, ppath, by_types, types, spec.params)
                elif param.type == "enum":
                    if param.values is None:
                        self.error(ppath, "an enum parameter needs `values`")
                    self.value(param.values, f"{ppath}.values", BASE | {"actor", "params"}, types, spec.params)
                for key in ("min", "max", "default"):
                    self.value(getattr(param, key), f"{ppath}.{key}", BASE | {"actor", "params"}, types, spec.params)
                if param.type not in ("number", "int") and (param.min is not None or param.max is not None):
                    self.error(ppath, "min/max apply to number and int parameters")
                if param.step is not None and param.type not in ("number", "int"):
                    self.error(f"{ppath}.step", "step applies to number and int parameters")
            check_param_bounds(self, path, spec)
            for index, condition in enumerate(spec.when):
                self.expr(condition.expr, f"{path}.when[{index}]", BASE | {"actor", "params"}, types, spec.params)
            roots = set(BASE | {"actor", "params"})
            self.value(spec.chance, f"{path}.chance", roots, types, spec.params)
            check_literal_probability(self, spec.chance, f"{path}.chance")
            self.value(spec.duration, f"{path}.duration", roots, types, spec.params)
            if spec.duration is not None and self.c.clock.mode != "continuous":
                self.warn(f"{path}.duration", "duration only applies with a continuous clock")
            after = self.effects(spec.do, f"{path}.do", roots, dict(types), spec.params)
            after |= self.effects(spec.otherwise, f"{path}.otherwise", roots, dict(types), spec.params)
            if spec.otherwise and spec.chance is None:
                self.warn(f"{path}.otherwise", "runs only when `chance` fails, and there is no `chance`")
            for key in ("outcome", "announce"):
                self.template(getattr(spec, key), f"{path}.{key}", None, after, types, spec.params)
            if isinstance(spec.terminal, str):
                self.expr(spec.terminal, f"{path}.terminal", after, types, spec.params)
            for pname, param in spec.params.items():
                if param.overflow == "truncate" and (param.type != "text" or param.max_len is None):
                    self.error(f"{path}.params.{pname}.overflow", "`truncate` cuts text to `max_len`",
                               "use it on a parameter of type text with a max_len")
                self.template(param.invalid, f"{path}.params.{pname}.invalid", None,
                              BASE | {"actor", "params", "value"}, types, spec.params)
            if not any(name in _stage_action_names(s, self.c) for s in self.c.stage_list()):
                self.warn(path, "is not available in any stage", "add it to a stage's `actions`")

    def _tool_group(self: "_Checker", spec: C.ActionSpec, path: str) -> None:  # type: ignore[misc]
        """An action offered inside a shared tool: the tool's name is free, and `action` is the tool's own argument."""
        tool = spec.tool or ""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tool):
            self.error(f"{path}.tool", f"'{tool}' is not a tool name", "use letters, digits and _, starting with a letter")
        elif tool in self.c.actions:
            self.error(f"{path}.tool", f"'{tool}' is also the name of an action", "give the shared tool another name")
        elif tool in ("look", "inspect", "end_turn"):
            self.error(f"{path}.tool", f"'{tool}' is a built-in tool", "give the shared tool another name")
        if "action" in spec.params:
            self.error(f"{path}.params.action", "an action inside a shared tool cannot take a parameter named `action`",
                       "the tool's `action` argument picks the action; rename the parameter")

    def _list_param(self: "_Checker", param: C.ParamSpec, ppath: str, by_types: Set[str], types: Types,  # type: ignore[misc]
                    params: Mapping[str, C.ParamSpec]) -> None:
        item = param.items
        if item is not None:
            if item.type not in C.PARAM_TYPES:
                self.error(f"{ppath}.items.type", f"unknown type '{item.type}'", self._suggest(item.type, C.PARAM_TYPES))
                return
            if item.type == "list":
                self.error(f"{ppath}.items", "a list of lists is not supported", "use items of enum, entity, text, number, int or bool")
                return
        elif param.of is None and param.values is None:
            self.warn(ppath, "a list without `items`, `of` or `values` takes free-text items",
                      "say what each item is, e.g. \"values\": [...] or \"of\": \"card\"")
        if param.min_items is not None and param.max_items is not None and param.min_items > param.max_items:
            self.error(ppath, f"min_items ({param.min_items}) is more than max_items ({param.max_items})")
        if param.max_items is not None and param.max_items > C.MAX_LIST_ITEMS:
            self.error(f"{ppath}.max_items", f"is more than the limit of {C.MAX_LIST_ITEMS}")
        entity_of = item.of if item is not None and item.type == "entity" else (param.of if item is None else None)
        where = item.where if item is not None else param.where
        if (item is not None and item.type == "entity") or (item is None and param.of is not None):
            if entity_of is None:
                self.error(f"{ppath}.items", "entity items need `of` (the entity type)")
            elif self._type(entity_of, f"{ppath}.of"):
                self.expr(where, f"{ppath}.where", BASE | {"actor", "it", "i", "params"},
                          {"actor": by_types, "it": {entity_of}}, params)
        values = item.values if item is not None and item.type == "enum" else (param.values if item is None else None)
        if item is not None and item.type == "enum" and values is None:
            self.error(f"{ppath}.items", "enum items need `values`")
        self.value(values, f"{ppath}.values", BASE | {"actor", "params"}, types, params)

    def _stages(self: "_Checker") -> None:  # type: ignore[misc]
        seen: Set[str] = set()
        for index, stage in enumerate(self.c.stages):
            path = f"stages[{index}]"
            if stage.name in seen:
                self.error(f"{path}.name", f"duplicate stage name '{stage.name}'")
            seen.add(stage.name)
            names = _stage_action_names(stage, self.c, raw=True)
            for action in names:
                if action not in self.c.actions:
                    self.error(f"{path}.actions", f"'{action}' is not a declared action",
                               self._hint(action, self.c.actions, "actions"))
            if isinstance(stage.actions, dict):
                for type_name in stage.actions:
                    self._type(type_name, f"{path}.actions.{type_name}", agent=True)
            elif isinstance(stage.actions, str) and stage.actions != "all":
                self.error(f"{path}.actions", "use 'all', a list of action names, or {type: [actions]}")
            if stage.turns not in ("sequential", "simultaneous", "scheduled"):
                self.error(f"{path}.turns", f"unknown turns '{stage.turns}'", "sequential, simultaneous or scheduled")
            continuous = self.c.clock.mode == "continuous"
            if stage.turns == "scheduled" and not continuous:
                self.error(f"{path}.turns", "scheduled turns need a continuous clock", "set clock.mode to continuous")
            if (stage.interval is not None or stage.first_wake is not None) and stage.turns != "scheduled":
                self.warn(path, "interval and first_wake only apply to scheduled turns")
            self.value(stage.interval, f"{path}.interval", BASE | {"actor"}, {"actor": set(self.agents)})
            self.value(stage.first_wake, f"{path}.first_wake", BASE | {"it", "i"}, {"it": set(self.agents)})
            if stage.quiet not in ("wake", "skip"):
                self.error(f"{path}.quiet", f"unknown quiet '{stage.quiet}'", "wake or skip")
            if stage.max_actions < 1 or stage.max_calls < 1:
                self.error(path, "max_actions and max_calls must be at least 1")
            agent_types: Types = {"it": set(self.agents)}
            if stage.order not in ("seat", "random"):
                self.expr(stage.order, f"{path}.order", BASE | {"it", "i"}, agent_types)
            self.expr(stage.who, f"{path}.who", BASE | {"it", "i"}, agent_types)
            self.expr(stage.until, f"{path}.until", BASE)
            self.expr(stage.when, f"{path}.when", BASE)
            if isinstance(stage.passes, str):
                self.expr(stage.passes, f"{path}.passes", {"inputs"})
            self.template(stage.brief or None, f"{path}.brief", "actor", BASE | {"actor"}, {"actor": set(self.agents)})
            self.effects(stage.on_enter, f"{path}.on_enter", set(BASE), {})
            self.effects(stage.on_exit, f"{path}.on_exit", set(BASE), {})
            for hook in ("on_idle", "on_wake", "on_turn_end"):
                self.effects(getattr(stage, hook), f"{path}.{hook}", set(BASE) | {"actor"}, {"actor": set(self.agents)})
            check_stage_turns(self, stage, path, BASE)

    def _views(self: "_Checker") -> None:  # type: ignore[misc]
        if SPECTATOR in self.c.types:
            self.error(f"types.{SPECTATOR}", f"'{SPECTATOR}' is reserved for spectator views", "rename the type")
        for name, view in self.c.views.items():
            path = f"views.{name}"
            if spectator_audience_issues(self, name, view):
                check_spectator_view(self, name, view, BASE)
                continue
            targets = [view.for_] if isinstance(view.for_, str) else view.for_
            targets = [target for target in targets if target != SPECTATOR]
            actor_types = set(self.agents) if targets == ["all"] else {t for t in targets if self._type(t, f"{path}.for", agent=True)}
            types: Types = {"actor": actor_types}
            for stage in view.stages or []:
                if stage not in self.stage_names:
                    self.error(f"{path}.stages", f"'{stage}' is not a stage", self._hint(stage, self.stage_names, "stages"))
            self.expr(view.when, f"{path}.when", BASE | {"actor"}, types)
            if view.of is None:
                self.template(view.show, f"{path}.show", "actor", BASE | {"actor"}, types)
                continue
            item_roots = BASE | {"actor", "it", "i"}
            if view.of in self.c.types:
                types["it"] = {view.of}
            elif view.of in self.c.records:
                pass
            else:
                self.expr(view.of, f"{path}.of", BASE | {"actor"}, types)
            self.expr(view.where, f"{path}.where", item_roots, types)
            self.expr(view.sort, f"{path}.sort", item_roots, types)
            self.template(view.show, f"{path}.show", "it", item_roots, types)
            if view.of in self.c.types and view.where is None:
                self._private_listing(view.show, view.of, f"{path}.show")
            if view.limit is not None and view.limit < 1:
                self.error(f"{path}.limit", "must be at least 1")

    def _private_listing(self: "_Checker", show: str, of: str, path: str) -> None:  # type: ignore[misc]
        """Warn when a view lists every entity of a type with a private property: each reader sees everyone's."""
        try:
            compiled = compile_template(show, "it")
        except ExprError:
            return  # already reported by the template check
        specs = self.c.props_of(of)
        shown = sorted({chain[1] for expr in compiled.expressions for chain in expr.paths
                        if len(chain) > 1 and chain[0] == "it" and chain[1] in specs and specs[chain[1]].private})
        if shown:
            self.warn(path, f"shows private {', '.join(shown)} of every {of} to each reader",
                      "add a `where` choosing whose to show (e.g. `$it.id == $actor.id`), or leave the private field out")


def _stage_action_names(stage: C.StageSpec, contract: Contract, raw: bool = False) -> List[str]:
    if stage.actions == "all":
        return [] if raw else list(contract.actions)
    if isinstance(stage.actions, list):
        return list(stage.actions)
    if isinstance(stage.actions, dict):
        return [a for names in stage.actions.values() for a in names]
    return []
