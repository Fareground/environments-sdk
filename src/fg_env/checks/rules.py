"""Checking events, policies, measures, defs and blocks, arms, and calibration."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .. import contract as C
from ..contract import Contract
from ..effects.statements import RESERVED_ROOTS
from ..expr import FUNCTIONS, ExprError, compile_expr
from ..expr.template import FORMATS, compile_template
from ..sampling.probability import check_literal_probability
from .roots import BASE

if TYPE_CHECKING:
    from . import _Checker
    from .roots import Types

__all__ = ["RuleChecks"]

#: The rounds a `when` fires on: one (`$round == 5`, `$round == $inputs.day`) or listed (`$round in [2, 4]`).
_ON_ROUNDS = re.compile(r"\$round\s*(?:==\s*(\d+|\$inputs\.[A-Za-z_]\w*)|\s+in\s+(\[[\d,\s]*\]))")


def scheduled_rounds(when: str | None) -> list[str]:
    """The rounds (as expressions) a `when` fires on alone, when it names them; empty when it may fire on others."""
    if when is None or " or " in when:
        return []
    return [one or listed for one, listed in _ON_ROUNDS.findall(when)]


class RuleChecks:
    """The event, policy, measure, def, arm and calibration sections of a contract (mixed into the contract
    checker)."""

    def _events(self: _Checker) -> None:  # type: ignore[misc]
        for index, event in enumerate(self.c.events):
            path = f"events[{index}]"
            roots, types = self._anchor(event.on, f"{path}.on")
            if event.once and event.on.startswith(("create.", "remove.")):
                self.error(f"{path}.once", f"an event on '{event.on}' fires for every entity, so `once` does not "
                                           "apply", "keep a world flag, and check it in `when`")
            self.condition(event.when, f"{path}.when", roots, types)
            self._after_the_clock(event.when, event.name, f"{path}.when")
            self.effects(event.do, f"{path}.do", set(roots), types)
            loop = event.do[0] if len(event.do) == 1 and isinstance(event.do[0], dict) else {}
            item = loop.get("as") or "it"
            if "each" in loop and event.say is not None and _reads_root(event.say, item):
                self.error(f"{path}.say", f"reads ${item}, but `say` is one headline for the whole event, told once "
                                          "after every item's `do`",
                           f'to tell news per item, emit it in `do`: {{"emit": "news", "say": "…{{${item}.name}}…"}}')
            else:
                self.template(event.say, f"{path}.say", None, roots, types)
            self._shared_text(event.say, f"{path}.say", {})
            if not event.do and not event.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _anchor(self: _Checker, anchor: str, path: str) -> tuple[frozenset[str], Types]:  # type: ignore[misc]
        """The roots and item types an event on ``anchor`` reads, after checking the stage or type it names."""
        kind, _, rest = anchor.partition(".")
        if kind == "stage":
            stage, _, point = rest.rpartition(".")
            names = [s.name for s in self.c.stage_list()]
            if stage not in names:
                self.error(path, f"there is no stage '{stage}'", self._hint(stage, names, "stages"))
            if point == "turn":
                return BASE | {"actor", "acted", "timed_out"}, {"actor": set(self.agents)}
        elif kind in ("create", "remove"):
            if self._type(rest, path):
                return BASE | {"it"}, {"it": set(self.c.subtypes(rest))}
            return BASE | {"it"}, {}
        return BASE, {}

    def _after_the_clock(self: _Checker, when: str | None, name: str | None, path: str) -> None:  # type: ignore[misc]
        """An event whose `when` holds only on rounds past the clock's last never fires in a run of the clock's
        length."""
        rounds = self.c.clock.rounds
        if not isinstance(rounds, int):
            return
        planned = [int(r) for text in scheduled_rounds(when) for r in re.findall(r"^\d+$|(?<=[\[,\s])\d+", text)]
        if not planned or min(planned) <= rounds:
            return
        what = f"event '{name}'" if name else "this event"
        self.warn(path, f"{what} fires at round {min(planned)}, after the clock's last round {rounds}, so it never "
                        "fires", f"use a round up to {rounds}, or lengthen clock.rounds")

    def _policies(self: _Checker) -> None:  # type: ignore[misc]
        for name, policy in self.c.policies.items():
            for index, rule in enumerate(policy.rules):
                path = f"policies.{name}.rules[{index}]"
                action = self.c.actions.get(rule.do)
                if rule.do != "pass" and action is None:
                    self.error(f"{path}.do", f"'{rule.do}' is not a declared action",
                               self._hint(rule.do, self.c.actions, "actions"))
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
                self.condition(rule.when, f"{path}.when", rule_roots, actor_types)
                self.value(rule.chance, f"{path}.chance", rule_roots, actor_types)
                check_literal_probability(self, rule.chance, f"{path}.chance")
                self.value(rule.with_, f"{path}.with", rule_roots, actor_types)

    def _measure(self: _Checker) -> None:  # type: ignore[misc]
        for name, metric in self.c.metrics.items():
            self.expr(metric.expr, f"metrics.{name}", BASE)
        for name, output in self.c.outputs.items():
            path = f"outputs.{name}"
            if output.format and output.format not in FORMATS:
                self.error(f"{path}.format", f"unknown format '{output.format}'",
                           self._suggest(output.format, FORMATS) or ", ".join(FORMATS))
            if output.type not in C.OUTPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{output.type}'",
                           self._suggest_type(output.type, C.OUTPUT_TYPES))
            self.expr(output.expr, path, BASE | {"outputs", "result"})
        for index, end in enumerate(self.c.end):
            self.condition(end.when, f"end[{index}].when", BASE)
            if _at_last_round(end.when, self.c.clock.rounds):
                self.warn(f"end[{index}].when",
                          f"`{end.when}` holds as the last round starts: `end` is checked after the start events, so "
                          "the run stops before the last round's stages play",
                          "remove it: the run ends by itself after its last round (clock.rounds); to name a winner "
                          "then, end in an end-phase event: "
                          '{"phase": "end", "do": {"if": "$round == $clock.rounds", "then": {"end": "final", "winner": '
                          '...}}}')
            self.expr(end.winner, f"end[{index}].winner", BASE)
            self.template(end.say, f"end[{index}].say", None, BASE)
            if end.check not in C.END_CHECKS:
                self.error(f"end[{index}].check", f"unknown check '{end.check}'",
                           self._suggest(end.check, C.END_CHECKS) or ", ".join(C.END_CHECKS))
        for index, invariant in enumerate(self.c.invariants):
            self.condition(invariant.expr, f"invariants[{index}]", BASE)
            self.template(invariant.why or None, f"invariants[{index}].why", None, BASE)
            if invariant.check not in C.INVARIANT_CHECKS:
                self.error(f"invariants[{index}].check", f"unknown check '{invariant.check}'",
                           self._suggest(invariant.check, C.INVARIANT_CHECKS) or ", ".join(C.INVARIANT_CHECKS))
        if not self.c.outputs:
            self.warn("outputs", "no outputs declared", "declare the typed results this environment produces")

    def _defs_and_blocks(self: _Checker) -> None:  # type: ignore[misc]
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
            bare: set[str] = set()
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

    def _arms(self: _Checker) -> None:  # type: ignore[misc]
        for name, arm in self.c.arms.items():
            for key in arm.inputs:
                if key not in self.c.inputs:
                    self.error(f"arms.{name}.inputs.{key}", f"'{key}' is not a declared input",
                               self._hint(key, self.c.inputs, "inputs"))
            for key in arm.patch:
                if key not in Contract.model_fields:
                    self.error(f"arms.{name}.patch.{key}", f"'{key}' is not a contract section",
                               self._suggest(key, Contract.model_fields))

    def _calibration(self: _Checker) -> None:  # type: ignore[misc]
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
                self.error(path, "has no range",
                           f"give {{\"low\": …, \"high\": …}} or declare min and max on inputs.{name}")
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
                self.error(path, f"'{measure}' is not an output or metric",
                           self._hint(measure, measures, "outputs and metrics"))
            self.value(target.get("value") if isinstance(target, dict) else target, path, BASE)


_ROUND_IS = re.compile(r"\s*\$round\s*(?:==|>=)\s*(.+?)\s*")


def _at_last_round(when: str, rounds: Any) -> bool:
    """Whether an `end` condition only says the round is the clock's last one (`$round == $clock.rounds`, or the
    number or input the clock's `rounds` is)."""
    match = _ROUND_IS.fullmatch(when)
    return match is not None and match.group(1) in ("$clock.rounds", str(rounds))


def _reads_root(template: str, root: str) -> bool:
    """Whether a template reads ``$<root>`` (False when it does not compile: the template check reports that)."""
    try:
        return any(root in expr.roots for expr in compile_template(template, None).expressions)
    except ExprError:
        return False
