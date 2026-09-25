"""Checking events, policies, measures, defs and arms."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .. import contract as C
from ..contract import Contract
from ..effects.statements import RESERVED_ROOTS
from ..expr import ExprError, Scope, compile_expr
from ..expr.calls import callable_in
from ..expr.template import FORMATS, compile_template
from ..sampling.probability import check_literal_probability
from .effects import EffectChecks, broadcasts
from .roots import BASE

if TYPE_CHECKING:
    from .roots import Types

__all__ = ["RuleChecks"]

#: The rounds a `when` fires on: one (`$round == 5`, `$round == $inputs.day`), listed (`$round in [2, 4]`), or the
#: first of those it may fire on (`$round >= 5`, as a prediction market's `resolve_at` is written).
_ON_ROUNDS = re.compile(r"\$round\s*(?:(?:==|>=)\s*\(?(\d+|\$inputs\.[A-Za-z_]\w*)\)?|\s+in\s+(\[[\d,\s]*\]))")


def scheduled_rounds(when: str | None) -> list[str]:
    """The rounds (as expressions) a `when` fires on alone, when it names them; empty when it may fire on others."""
    if when is None or " or " in when:
        return []
    return [one or listed for one, listed in _ON_ROUNDS.findall(when)]


class RuleChecks(EffectChecks):
    """The event, policy, measure, def and arm sections of a contract (a part of the contract checker)."""

    def _events(self) -> None:
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
            if event.say or broadcasts(event.do, self.c):
                self._private_gate(event.when, f"{path}.when", "whether it fired (it sends everyone news)", types)
            if not event.do and not event.say:
                self.warn(path, "does nothing", "add `do` or `say`")

    def _anchor(self, anchor: str, path: str) -> tuple[frozenset[str], Types]:
        """The roots and item types an event on ``anchor`` reads, after checking the stage or type it names."""
        kind, _, rest = anchor.partition(".")
        if kind == "stage":
            stage, _, point = rest.rpartition(".")
            names = [s.name for s in self.c.stage_list()]
            if stage not in names:
                self.error(path, f"there is no stage '{stage}'", self._hint(stage, names, "stages"))
            if point == "turn":
                return BASE | set(C.anchor_roots(anchor)), {"actor": set(self.agents)}
        elif kind in ("create", "remove"):
            if self._type(rest, path):
                return BASE | set(C.anchor_roots(anchor)), {"it": set(self.c.subtypes(rest))}
        return BASE | set(C.anchor_roots(anchor)), {}

    def _after_the_clock(self, when: str | None, name: str | None, path: str) -> None:
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
                        f"fires — in `{when}`", f"use a round up to {rounds}, or lengthen clock.rounds")

    def _policies(self) -> None:
        for owner, spec in self.c.types.items():
            players = [kind for kind in self.c.subtypes(owner) if kind in self.agents]
            for name, policy in spec.policies.items():
                self._policy(f"types.{owner}.policies.{name}", policy, owner, players)

    def _policy(self, base: str, policy: C.PolicySpec, owner: str, players: list[str]) -> None:
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

    def _measure(self) -> None:
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
            self._unready_outputs(name, output.expr, path)
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
            if _not_true_or_false(invariant.expr):
                self.error(f"invariants[{index}]", f"`{invariant.expr}` is a constant that is not true or false, so it "
                                                    "states nothing: an invariant is a law of the state",
                           "write the law you mean over the state, e.g. `$world.pot >= 0` or "
                           "`$sum(player, $it.cash) == $inputs.total`")
            self.template(invariant.why or None, f"invariants[{index}].why", None, BASE)
            if invariant.check not in C.INVARIANT_CHECKS:
                self.error(f"invariants[{index}].check", f"unknown check '{invariant.check}'",
                           self._suggest(invariant.check, C.INVARIANT_CHECKS) or ", ".join(C.INVARIANT_CHECKS))
        if not self.c.outputs:
            self.warn("outputs", "no outputs declared", "declare the typed results this environment produces")

    def _unready_outputs(self, name: str, source: str, path: str) -> None:
        """An output reads `$outputs.<o>` as worked out so far: outputs are worked out in the order written (a series
        output's latest sample is there all along), so an output that is not a series, read by itself or by one
        written before it (every loop of outputs has such a read), never has a value there."""
        try:
            reads = {chain[1] for chain in compile_expr(source).paths if chain[0] == "outputs" and len(chain) > 1}
        except ExprError:
            return  # reported by the expression check
        names = list(self.c.outputs)
        later = sorted(read for read in reads if read in self.c.outputs and not self.c.outputs[read].series
                       and names.index(read) >= names.index(name))
        if later:
            which = "itself" if later == [name] else ", ".join(f"$outputs.{read}" for read in later)
            self.error(path, f"reads {which}, which has no value yet when {name} is worked out: outputs are worked "
                             "out in the order written",
                       "read what it is worked out from instead, or move the output it reads before it")

    def _defs(self) -> None:
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
            if callable_in(name, self.families):
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

    def _arms(self) -> None:
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


def _not_true_or_false(source: str) -> bool:
    """Whether an expression reads nothing (no root, no function) and gives something other than true or false."""
    try:
        expr = compile_expr(source)
        if expr.roots or expr.functions or expr.methods:
            return False
        return not isinstance(expr(Scope()), bool)
    except ExprError:
        return False  # reported by the condition check
