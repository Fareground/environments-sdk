"""Checking events, triggers, policies, measures, defs and blocks, arms, and calibration."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Set

from . import contract as C
from .check_roots import BASE
from .check_space import check_event_order
from .contract import Contract
from .effects import RESERVED_ROOTS
from .expr import FUNCTIONS, ExprError, compile_expr
from .template import FORMATS

if TYPE_CHECKING:
    from .check import _Checker
    from .check_roots import Types

__all__ = ["RuleChecks"]


class RuleChecks:
    """The event, trigger, policy, measure, def, arm and calibration sections of a contract (mixed into the
    contract checker)."""

    def _events(self: "_Checker") -> None:  # type: ignore[misc]
        for index, event in enumerate(self.c.events):
            path = f"events[{index}]"
            if event.phase not in ("start", "end"):
                self.error(f"{path}.phase", f"unknown phase '{event.phase}'", "start or end")
            for arm in event.arms or []:
                if arm not in self.c.arms:
                    self.error(f"{path}.arms", f"'{arm}' is not a declared arm", self._hint(arm, self.c.arms, "arms"))
            self.value(event.at, f"{path}.at", BASE)
            self.expr(event.when, f"{path}.when", BASE)
            if isinstance(event.every, str):
                self.expr(event.every, f"{path}.every", {"inputs"})
            elif event.every is not None and event.every < 1:
                self.error(f"{path}.every", "must be at least 1")
            self.value(event.chance, f"{path}.chance", BASE)
            types: Types = {}
            roots = set(BASE)
            if event.each is not None:
                item = event.as_ or "it"
                roots |= {item, "i"}
                if event.each in self.c.types:
                    types[item] = {event.each}
                else:
                    self.expr(event.each, f"{path}.each", BASE)
            self.expr(event.where, f"{path}.where", roots, types)
            self.effects(event.do, f"{path}.do", roots, types)
            check_event_order(self, event, path, frozenset(roots), types)
            self.template(event.say, f"{path}.say", None, BASE)
            if not event.do and not event.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _triggers(self: "_Checker") -> None:  # type: ignore[misc]
        for index, trigger in enumerate(self.c.triggers):
            path = f"triggers[{index}]"
            for arm in trigger.arms or []:
                if arm not in self.c.arms:
                    self.error(f"{path}.arms", f"'{arm}' is not a declared arm", self._hint(arm, self.c.arms, "arms"))
            self.expr(trigger.when, f"{path}.when", BASE)
            self.effects(trigger.do, f"{path}.do", set(BASE), {})
            self.template(trigger.say, f"{path}.say", None, BASE)
            if not trigger.do and not trigger.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _policies(self: "_Checker") -> None:  # type: ignore[misc]
        for name, policy in self.c.policies.items():
            for index, rule in enumerate(policy.rules):
                path = f"policies.{name}.rules[{index}]"
                action = self.c.actions.get(rule.do)
                if rule.do != "pass" and action is None:
                    self.error(f"{path}.do", f"'{rule.do}' is not a declared action", self._hint(rule.do, self.c.actions, "actions"))
                actor_types: Types = {"actor": set(self.agents)}
                if action is not None:
                    actor_types = {"actor": set([action.by] if isinstance(action.by, str) else action.by)}
                    for key in rule.with_:
                        if key not in action.params:
                            self.error(f"{path}.with.{key}", f"'{rule.do}' has no parameter '{key}'",
                                       self._suggest(key, action.params))
                rule_roots = BASE | {"actor"}
                if rule.each is not None:
                    if rule.each not in self.c.types:
                        self.expr(rule.each, f"{path}.each", BASE | {"actor"}, actor_types)
                    else:
                        actor_types = {**actor_types, "it": set(self.c.subtypes(rule.each))}
                    rule_roots = rule_roots | {"it", "i"}
                self.expr(rule.when, f"{path}.when", rule_roots, actor_types)
                self.value(rule.chance, f"{path}.chance", rule_roots, actor_types)
                self.value(rule.with_, f"{path}.with", rule_roots, actor_types)

    def _measure(self: "_Checker") -> None:  # type: ignore[misc]
        for name, metric in self.c.metrics.items():
            self.expr(metric.expr, f"metrics.{name}", BASE)
        for name, output in self.c.outputs.items():
            path = f"outputs.{name}"
            if output.format and output.format not in FORMATS:
                self.error(f"{path}.format", f"unknown format '{output.format}'",
                           self._suggest(output.format, FORMATS) or ", ".join(FORMATS))
            if output.type not in C.OUTPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{output.type}'", self._suggest(output.type, C.OUTPUT_TYPES))
            self.expr(output.expr, path, BASE | {"outputs", "result"})
        for index, end in enumerate(self.c.end):
            self.expr(end.when, f"end[{index}].when", BASE)
            self.expr(end.winner, f"end[{index}].winner", BASE)
            self.template(end.say, f"end[{index}].say", None, BASE)
            if end.check not in C.END_CHECKS:
                self.error(f"end[{index}].check", f"unknown check '{end.check}'",
                           self._suggest(end.check, C.END_CHECKS) or ", ".join(C.END_CHECKS))
        for index, invariant in enumerate(self.c.invariants):
            self.expr(invariant.expr, f"invariants[{index}]", BASE)
            if invariant.check not in C.INVARIANT_CHECKS:
                self.error(f"invariants[{index}].check", f"unknown check '{invariant.check}'",
                           self._suggest(invariant.check, C.INVARIANT_CHECKS) or ", ".join(C.INVARIANT_CHECKS))
        if not self.c.outputs:
            self.warn("outputs", "no outputs declared", "declare the typed results this environment produces")

    def _defs_and_blocks(self: "_Checker") -> None:  # type: ignore[misc]
        for name, spec in self.c.defs.items():
            path = f"defs.{name}"
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                self.error(path, "def names are letters, digits and underscores")
            if name in FUNCTIONS:
                self.warn(path, f"'{name}' shadows the built-in ${name}; this contract's def is used",
                          "rename it if you meant the built-in")
            for arg in spec.args:
                if arg in BASE or arg in RESERVED_ROOTS:
                    self.error(f"{path}.args", f"'{arg}' is a built-in root", "choose another argument name")
            self.expr(spec.expr, f"{path}.expr", BASE | set(spec.args))
            bare: Set[str] = set()
            try:
                bare = set(compile_expr(spec.expr).symbols) & set(spec.args)
            except ExprError:
                pass
            for arg in sorted(bare):
                self.error(f"{path}.expr", f"argument '{arg}' is written without $, so it is the text '{arg}'",
                           f"write ${arg}")
        for name, block in self.c.blocks.items():
            path = f"blocks.{name}"
            for arg in block.args:
                if arg in BASE or arg in RESERVED_ROOTS:
                    self.error(f"{path}.args", f"'{arg}' is a built-in root", "choose another argument name")
            self.effects(block.do, f"{path}.do", set(BASE) | set(block.args), {})

    def _arms(self: "_Checker") -> None:  # type: ignore[misc]
        for name, arm in self.c.arms.items():
            for key in arm.inputs:
                if key not in self.c.inputs:
                    self.error(f"arms.{name}.inputs.{key}", f"'{key}' is not a declared input", self._hint(key, self.c.inputs, "inputs"))
            for key in arm.patch:
                if key not in Contract.model_fields:
                    self.error(f"arms.{name}.patch.{key}", f"'{key}' is not a contract section",
                               self._suggest(key, Contract.model_fields))

    def _calibration(self: "_Checker") -> None:  # type: ignore[misc]
        spec = self.c.calibration
        if spec is None:
            return
        for name, given in spec.params.items():
            path = f"calibration.params.{name}"
            declared = self.c.inputs.get(name)
            if declared is None:
                self.error(path, f"'{name}' is not a declared input", self._hint(name, self.c.inputs, "inputs"))
                continue
            if declared.type not in ("number", "int"):
                self.error(path, f"'{name}' is a {declared.type} input; only number and int inputs can be fitted")
                continue
            extra = sorted(set(given) - {"low", "high", "log"})
            if extra:
                self.error(path, f"unknown key(s) {', '.join(extra)}", "give low, high and log")
            low, high = given.get("low", declared.min), given.get("high", declared.max)
            if low is None or high is None:
                self.error(path, "has no range", f"give {{\"low\": …, \"high\": …}} or declare min and max on inputs.{name}")
            elif not low < high:
                self.error(path, f"low {low} must be below high {high}")
        for name in spec.inputs:
            if name not in self.c.inputs:
                self.error(f"calibration.inputs.{name}", f"'{name}' is not a declared input",
                           self._hint(name, self.c.inputs, "inputs"))
            elif name in spec.params:
                self.error(f"calibration.inputs.{name}", f"'{name}' is fitted; a pilot input cannot also fix it")
        measures = {**self.c.outputs, **self.c.metrics}
        for name, target in spec.targets.items():
            path = f"calibration.targets.{name}"
            measure = str(target.get("of", name)) if isinstance(target, dict) and "stat" in target else name
            measure = measure[len("series."):] if measure.startswith("series.") else measure
            if measure not in measures:
                self.error(path, f"'{measure}' is not an output or metric", self._hint(measure, measures, "outputs and metrics"))
            self.value(target.get("value") if isinstance(target, dict) else target, path, BASE)
