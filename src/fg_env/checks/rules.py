"""Checking events, triggers, policies, measures, defs and arms."""
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
from .space import check_event_order

if TYPE_CHECKING:
    from . import _Checker
    from .roots import Types

__all__ = ["RuleChecks"]


class RuleChecks:
    """The event, trigger, policy, measure, def and arm sections of a contract (mixed into the
    contract checker)."""

    def _events(self: _Checker) -> None:  # type: ignore[misc]
        for index, event in enumerate(self.c.events):
            path = f"events[{index}]"
            if event.phase not in ("start", "end"):
                self.error(f"{path}.phase", f"unknown phase '{event.phase}'",
                           self._suggest(event.phase, ("start", "end")) or "start or end")
            for arm in event.arms or []:
                if arm not in self.c.arms:
                    self.error(f"{path}.arms", f"'{arm}' is not a declared arm", self._hint(arm, self.c.arms, "arms"))
            self.value(event.at, f"{path}.at", BASE)
            self._after_the_clock(event.at, event.name, path)
            self.condition(event.when, f"{path}.when", BASE)
            self._count(event.every, f"{path}.every")
            types: Types = {}
            roots = set(BASE)
            if event.each is not None:
                item = event.as_ or "it"
                roots |= {item, "i"}
                if event.each in self.c.types:
                    types[item] = {event.each}
                else:
                    self.expr(event.each, f"{path}.each", BASE)
            self.condition(event.where, f"{path}.where", roots, types)
            self.effects(event.do, f"{path}.do", roots, types)
            check_event_order(self, event, path, frozenset(roots), types)
            if event.each is not None and event.say is not None and _reads_root(event.say, event.as_ or "it"):
                item = event.as_ or "it"
                self.error(f"{path}.say", f"reads ${item}, but `say` is one headline for the whole event, told once "
                                          "after every item's `do`",
                           f'to tell news per item, emit it in `do`: {{"emit": "news", "say": "…{{${item}.name}}…"}}')
            else:
                self.template(event.say, f"{path}.say", None, BASE)
            self._shared_text(event.say, f"{path}.say", {})
            if not event.do and not event.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _after_the_clock(self: _Checker, at: Any, name: str | None, path: str) -> None:  # type: ignore[misc]
        """An event whose every round is past the clock's last never fires in a run of the clock's length."""
        rounds, planned = self.c.clock.rounds, at if isinstance(at, list) else [at]
        if not isinstance(rounds, int) or not planned or not all(isinstance(r, int) and r > rounds for r in planned):
            return
        what = f"event '{name}'" if name else "this event"
        self.warn(f"{path}.at", f"{what} fires at round {min(planned)}, after the clock's last round {rounds}, so it "
                                "never fires", f"use a round up to {rounds}, or lengthen clock.rounds")

    def _triggers(self: _Checker) -> None:  # type: ignore[misc]
        for index, trigger in enumerate(self.c.triggers):
            path = f"triggers[{index}]"
            for arm in trigger.arms or []:
                if arm not in self.c.arms:
                    self.error(f"{path}.arms", f"'{arm}' is not a declared arm", self._hint(arm, self.c.arms, "arms"))
            self.condition(trigger.when, f"{path}.when", BASE)
            self.effects(trigger.do, f"{path}.do", set(BASE), {})
            self.template(trigger.say, f"{path}.say", None, BASE)
            self._shared_text(trigger.say, f"{path}.say", {})
            if not trigger.do and not trigger.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _policies(self: _Checker) -> None:  # type: ignore[misc]
        for owner, spec in self.c.types.items():
            players = [kind for kind in self.c.subtypes(owner) if kind in self.agents]
            for name, policy in spec.policies.items():
                self._policy(f"types.{owner}.policies.{name}", policy, owner, players)

    def _policy(self: _Checker, base: str, policy: C.PolicySpec, owner: str,  # type: ignore[misc]
                players: list[str]) -> None:
        if not players:
            self.error(base, f"'{owner}' is not an agent type, so no agent plays this policy",
                       "declare it under an agent type's `policies`")
        for index, rule in enumerate(policy.rules):
            path = f"{base}.rules[{index}]"
            action = self.c.actions.get(rule.do)
            if rule.do != "pass" and action is None:
                self.error(f"{path}.do", f"'{rule.do}' is not a declared action",
                           self._hint(rule.do, self.c.actions, "actions"))
            actor_types: Types = {"actor": set(players)}
            if action is not None:
                takers = {kind for kind in players if self.c.can_take(kind, rule.do)}
                if players and not takers:
                    by = action.by if isinstance(action.by, str) else (action.by or [owner])[0]
                    self.error(f"{path}.do", f"no {owner} agent can take '{rule.do}' (it is taken by {by})",
                               f"declare the policy under types.{by}.policies, or call an action {owner} agents "
                               "take")
                actor_types = {"actor": takers or set(players)}
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
        for name, output in self.c.outputs.items():
            path = f"outputs.{name}"
            if output.format and output.format not in FORMATS:
                self.error(f"{path}.format", f"unknown format '{output.format}'",
                           self._suggest(output.format, FORMATS) or ", ".join(FORMATS))
            if output.type not in C.OUTPUT_TYPES:
                self.error(f"{path}.type", f"unknown type '{output.type}'",
                           self._suggest_type(output.type, C.OUTPUT_TYPES))
            self.expr(output.expr, path, BASE | ({"result"} if output.series is not True else set()))
            if isinstance(output.series, str):
                self.expr(output.series, f"{path}.series", BASE)
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

    def _defs(self: _Checker) -> None:  # type: ignore[misc]
        for name, spec in self.c.defs.items():
            path = f"defs.{name}"
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                self.error(path, "def names are letters, digits and underscores")
            for arg in spec.args:
                if arg in BASE or arg in RESERVED_ROOTS:
                    self.error(f"{path}.args", f"'{arg}' is a built-in root", "choose another argument name")
            if spec.do is not None:
                self.effects(spec.do, f"{path}.do", set(BASE) | set(spec.args), {})
                continue
            if name in FUNCTIONS:
                self.warn(path, f"'{name}' shadows the built-in ${name}; this contract's def is used",
                          "rename it if you meant the built-in")
            self.expr(spec.expr, f"{path}.expr", BASE | set(spec.args))
            bare: set[str] = set()
            try:
                bare = set(compile_expr(spec.expr).symbols) & set(spec.args)
            except ExprError:
                pass
            for arg in sorted(bare):
                self.error(f"{path}.expr", f"argument '{arg}' is written without $, so it is the text '{arg}'",
                           f"write ${arg}")

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
