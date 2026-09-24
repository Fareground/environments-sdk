"""Checking actions and their parameters, stages, and views."""
from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any, List, Mapping, Set

from .. import contract as C
from .params import check_param_bounds
from ..probability import check_literal_probability
from .roots import BASE
from .turns import check_spectator_view, check_stage_turns, spectator_audience_issues
from ..contract import Contract
from ..expr import is_expr
from ..perception import SPECTATOR
from ..reads import READS
from ..session import END_TURN

if TYPE_CHECKING:
    from . import _Checker
    from .roots import Types

__all__ = ["ActionChecks"]

#: Tools every turn may offer beside the actions.
BUILT_IN_TOOLS = (*READS, END_TURN)
#: The tool names model providers accept (Anthropic and OpenAI alike).
_PROVIDER_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_TURNS = ("sequential", "simultaneous", "scheduled")


class ActionChecks:
    """The action, stage and view sections of a contract (mixed into the contract checker)."""

    def _actions(self: "_Checker") -> None:  # type: ignore[misc]
        for name, spec in self.c.actions.items():
            path = f"actions.{name}"
            if "{$" in spec.description:
                self.warn(f"{path}.description", "action descriptions are static: {$...} remains literal",
                          "Put dynamic instructions in brief.roles.<actor type> or views.show; inspect env.preview(actor_id) to verify the actual text")
            by = [spec.by] if isinstance(spec.by, str) else spec.by
            by_types = {t for t in by if self._type(t, f"{path}.by", agent=True)}
            types: Types = {"actor": by_types}
            if spec.tool is None:
                self._tool_name(name, path)
            else:
                self._tool_group(spec, path)
            for pname, param in spec.params.items():
                ppath = f"{path}.params.{pname}"
                if param.type not in C.PARAM_TYPES:
                    self.error(f"{ppath}.type", f"unknown type '{param.type}'", self._suggest_type(param.type, C.PARAM_TYPES))
                    continue
                if param.type == "entity":
                    if param.of is None:
                        self.error(ppath, "an entity parameter needs `of` (the entity type)")
                    elif self._type(param.of, f"{ppath}.of"):
                        self.condition(param.where, f"{ppath}.where", BASE | {"actor", "it", "i", "params"},
                                  {"actor": by_types, "it": {param.of}}, spec.params)
                        self._private_filter(param.where, param.of, f"{ppath}.where")
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
                self.condition(condition.expr, f"{path}.when[{index}]", BASE | {"actor", "params"}, types, spec.params)
                self.template(condition.why or None, f"{path}.when[{index}].why", None, BASE | {"actor", "params"}, types,
                              spec.params)
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
            self._private_action(spec, types, path)
            self._undecided_by_luck(spec, path)
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

    def _undecided_by_luck(self: "_Checker", spec: C.ActionSpec, path: str) -> None:  # type: ignore[misc]
        """Nothing that decides whether a call is allowed, or what its arguments may be, draws at random: the engine
        refuses it, since a refused call costs nothing and calling again would roll fresh luck."""
        from ..describe.walk import draws  # imported late: describe imports the API, which imports the checker

        texts = {f"when[{index}]": condition.expr for index, condition in enumerate(spec.when)}
        texts.update({f"when[{index}].why": condition.why for index, condition in enumerate(spec.when)})
        for pname, param in spec.params.items():
            texts.update({f"params.{pname}.{key}": getattr(param, key)
                          for key in ("min", "max", "default", "values", "where", "invalid")})
        for key, text in texts.items():
            if isinstance(text, str) and draws(self.c, [text]):
                self.error(f"{path}.{key}", "draws at random, but it decides whether a call is allowed or what its "
                                            "arguments may be: a refused call costs nothing, so an agent could call "
                                            "again until luck let it through",
                           "draw in the action's `do` (or use its `chance`), or in an event that stores the result for "
                           "this to read")

    def _tool_group(self: "_Checker", spec: C.ActionSpec, path: str) -> None:  # type: ignore[misc]
        """An action offered inside a shared tool: the tool's name is free, and `action` is the tool's own argument."""
        tool = spec.tool or ""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tool):
            self.error(f"{path}.tool", f"'{tool}' is not a tool name", "use letters, digits and _, starting with a letter")
        elif tool in self.c.actions:
            self.error(f"{path}.tool", f"'{tool}' is also the name of an action", "give the shared tool another name")
        else:
            self._tool_name(tool, f"{path}.tool")
        if "action" in spec.params:
            self.error(f"{path}.params.action", "an action inside a shared tool cannot take a parameter named `action`",
                       "the tool's `action` argument picks the action; rename the parameter")

    def _tool_name(self: "_Checker", name: str, path: str) -> None:  # type: ignore[misc]
        """A name offered to models as a tool: not a built-in tool's, and one every provider accepts."""
        if name in BUILT_IN_TOOLS:
            self.error(path, f"'{name}' is a built-in tool, so a model could never call this one",
                       f"rename it, e.g. '{name}_action'")
        elif not _PROVIDER_NAME.fullmatch(name):
            self.error(path, f"'{name}' is not a tool name model providers accept: letters, digits, _ and - only, at "
                       "most 64 characters", f"rename it, e.g. '{_provider_name(name)}'")

    def _list_param(self: "_Checker", param: C.ParamSpec, ppath: str, by_types: Set[str], types: Types,  # type: ignore[misc]
                    params: Mapping[str, C.ParamSpec]) -> None:
        item = param.items
        if item is not None:
            if item.type not in C.PARAM_TYPES:
                self.error(f"{ppath}.items.type", f"unknown type '{item.type}'", self._suggest_type(item.type, C.PARAM_TYPES))
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
                self.condition(where, f"{ppath}.where", BASE | {"actor", "it", "i", "params"},
                          {"actor": by_types, "it": {entity_of}}, params)
                self._private_filter(where, entity_of, f"{ppath}.where")
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
            if stage.turns not in _TURNS:
                self.error(f"{path}.turns", f"unknown turns '{stage.turns}'",
                           self._suggest(stage.turns, _TURNS) or "sequential, simultaneous or scheduled")
            continuous = self.c.clock.mode == "continuous"
            if stage.turns == "scheduled" and not continuous:
                self.error(f"{path}.turns", "scheduled turns need a continuous clock", "set clock.mode to continuous")
            if (stage.interval is not None or stage.first_wake is not None) and stage.turns != "scheduled":
                self.warn(path, "interval and first_wake only apply to scheduled turns")
            self.value(stage.interval, f"{path}.interval", BASE | {"actor"}, {"actor": set(self.agents)})
            self.value(stage.first_wake, f"{path}.first_wake", BASE | {"it", "i"}, {"it": set(self.agents)})
            if stage.quiet not in ("wake", "skip"):
                self.error(f"{path}.quiet", f"unknown quiet '{stage.quiet}'",
                           self._suggest(stage.quiet, ("wake", "skip")) or "wake or skip")
            for setting in ("passes", "max_actions", "max_calls"):
                self._count(getattr(stage, setting), f"{path}.{setting}")
            agent_types: Types = {"it": set(self.agents)}
            self.order_setting(stage.order, f"{path}.order", ("seat", "random"), BASE | {"it", "i"}, agent_types)
            self.expr(stage.who, f"{path}.who", BASE | {"it", "i"}, agent_types)
            self.condition(stage.until, f"{path}.until", BASE)
            self.condition(stage.when, f"{path}.when", BASE)
            self.template(stage.brief or None, f"{path}.brief", "actor", BASE | {"actor"}, {"actor": set(self.agents)})
            self.effects(stage.on_enter, f"{path}.on_enter", set(BASE), {})
            self.effects(stage.on_exit, f"{path}.on_exit", set(BASE), {})
            for hook in ("on_idle", "on_wake", "on_turn_end"):
                self.effects(getattr(stage, hook), f"{path}.{hook}", set(BASE) | {"actor"}, {"actor": set(self.agents)})
            check_stage_turns(self, stage, path, BASE)
            self._sealed_announced(stage, path)
        self._open_stages()

    def _open_stages(self: "_Checker") -> None:  # type: ignore[misc]
        """A stage without `actions` offers every action — including ones another stage lists as its own, which
        agents can then take in the wrong phase. (An explicit `"actions": "all"` says every action is meant.)"""
        listed = {name for stage in self.c.stages if "actions" in stage.model_fields_set
                  for name in _stage_action_names(stage, self.c, raw=True)}
        for index, stage in enumerate(self.c.stages):
            if "actions" in stage.model_fields_set:
                continue
            elsewhere = [name for name in self.c.actions if name in listed]
            if elsewhere:
                self.warn(f"stages[{index}].actions",
                          f"is not set, so stage '{stage.name}' offers every action, including "
                          f"{', '.join(elsewhere[:5])}{' …' if len(elsewhere) > 5 else ''} that another stage lists",
                          "list the actions of this stage (\"actions\": [...]); write \"actions\": \"all\" if every "
                          "action belongs in it too")

    def _count(self: "_Checker", value: Any, path: str) -> None:  # type: ignore[misc]
        """A stage count setting: a whole number ≥ 1, or an expression over $inputs giving one."""
        if isinstance(value, str):
            self.expr(value, path, {"inputs"})
        elif isinstance(value, int) and value < 1:
            self.error(path, f"is {value}; it must be at least 1", "remove it for the default")

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
            self.condition(view.when, f"{path}.when", BASE | {"actor"}, types)
            if view.of is None:
                self.template(view.show, f"{path}.show", "actor", BASE | {"actor"}, types)
                continue
            item_roots = BASE | {"actor", "it", "i"}
            if view.of in self.c.types:
                types["it"] = {view.of}
            elif view.of in self.c.records:
                pass
            elif not is_expr(view.of):
                self.error(f"{path}.of", f"'{view.of}' is not a declared type or record",
                           self._suggest(view.of, [*self.c.types, *self.c.records])
                           or "name a type or record, or write an expression giving a list ($filter(...))")
            else:
                self.expr(view.of, f"{path}.of", BASE | {"actor"}, types)
            self.condition(view.where, f"{path}.where", item_roots, types)
            self.expr(view.sort, f"{path}.sort", item_roots, types)
            self.template(view.show, f"{path}.show", "it", item_roots, types)
            if view.of in self.c.types:
                self._private_listing(view, f"{path}.show")
            if view.limit is not None and view.limit < 1:
                self.error(f"{path}.limit", "must be at least 1")



def _stage_action_names(stage: C.StageSpec, contract: Contract, raw: bool = False) -> List[str]:
    if stage.actions == "all":
        return [] if raw else list(contract.actions)
    if isinstance(stage.actions, list):
        return list(stage.actions)
    if isinstance(stage.actions, dict):
        return [a for names in stage.actions.values() for a in names]
    return []


def _provider_name(name: str) -> str:
    """``name`` made into a tool name providers accept: accents dropped, other characters as _, at most 64."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", plain).strip("_")[:64] or "action"
