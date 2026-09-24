"""Checking reads of private properties in what agents are shown (mixed into the contract checker).

An agent's private property is shown only to that agent: the engine refuses reading another agent's in what one agent
is shown or offered, and any agent's in text sent to several. These checks report such reads before a run hits them.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .. import contract as C
from ..actions.book import stage_actions
from ..actions.reads import inspect_rule
from ..expr import Expr, ExprError, compile_expr
from ..expr.template import compile_template

if TYPE_CHECKING:
    from . import _Checker
    from .roots import Types

__all__ = ["PrivacyChecks"]


class PrivacyChecks:
    """Private properties in views, tools, outcomes, announcements and news (mixed into the contract checker)."""

    def _private_action(self: _Checker, spec: C.ActionSpec, types: Types, path: str) -> None:  # type: ignore[misc]
        """An action's announcement is sent to everyone; its outcome, `why`s and parameters' bounds, defaults and
        choices are what its actor is shown or offered, where a chosen agent's private property is refused whenever
        the actor chooses another agent; a requirement that reads one answers yes or no for free."""
        if not spec.private:
            self._shared_text(spec.announce, f"{path}.announce", types, spec.params)
        texts = {"outcome": spec.outcome}
        for pname, param in spec.params.items():
            texts.update({f"params.{pname}.{key}": getattr(param, key)
                          for key in ("min", "max", "min_items", "max_items", "default", "values")})
            texts[f"params.{pname}.invalid"] = param.invalid
        for index, condition in enumerate(spec.when):
            texts[f"when[{index}].why"] = condition.why
        for key, text in texts.items():
            read = self._agent_private_reads(_expressions(text), {}, spec.params, items=False)
            if read:
                self.error(f"{path}.{key}", f"reads private {', '.join(sorted(read))}: what the actor is shown or "
                                            "offered may read only its own private properties, so this is refused "
                                            "whenever it chooses another agent",
                           "work out what the actor may learn in `do` (`\"$seen = $params.target.role\"`) and show "
                           "`{$seen}`, or read only what it may know")
        for index, condition in enumerate(spec.when):
            expressions = _expressions(condition.expr)
            read = self._agent_private_reads(expressions, {}, spec.params) | self._fetched_private_reads(expressions)
            if read:
                self.warn(f"{path}.when[{index}]", f"refuses by private {', '.join(sorted(read))}: a refusal costs "
                                                   "the actor nothing, so calling again and again reads it out",
                          "decide by what the actor may know, or test it in `do` instead (`{\"if\": ..., \"then\": "
                          "[{\"fail\": ...}]}`): a refusal from `do` that read a private value spends the action")

    def _sealed_announced(self: _Checker, stage: C.StageSpec, path: str) -> None:  # type: ignore[misc]
        """A simultaneous stage announces each sealed choice to everyone by its action's name as it commits (unless
        the action is private or says what to announce): with more than one to choose from, each agent's choice —
        a secret ballot's vote — is public."""
        if stage.turns != "simultaneous":
            return
        for kind in self.c.agent_types():
            named = [name for name in stage_actions(self.c, stage, kind)
                     if not self.c.actions[name].private and self.c.actions[name].announce is None]
            if len(named) > 1:
                self.warn(path, f"announces each sealed choice to everyone by name as it commits (\"Ann: "
                                f"{named[0].replace('_', ' ')}.\"), so which of {', '.join(named)} each agent chose "
                                "is public",
                          "if the choice is secret (a ballot), give those actions `private: true` and announce only "
                          "the outcome (an `emit` in the stage's on_exit); if it is meant to be public, give them an "
                          "`announce`")
                return

    def _secret_subtypes(self: _Checker) -> None:  # type: ignore[misc]
        """An agent subtype with private actions of its own, of a type agents may inspect: inspect names each agent's
        type, so everyone can tell who is one."""
        warned: set[str] = set()
        for name, spec in self.c.actions.items():
            for kind in [spec.by] if isinstance(spec.by, str) else spec.by:
                if not spec.private or kind in warned or kind not in self.c.types or not self.c.is_agent(kind):
                    continue
                root = self.c.lineage(kind)[0]
                if root == kind or inspect_rule(self.c, kind) is False:
                    continue
                warned.add(kind)
                self.warn(f"types.{kind}", f"inspect names each agent's type, so everyone can tell who is a {kind}, "
                                           f"though its action {name} is private",
                          f"if being a {kind} is a secret, keep it in a private property of {root} instead of a "
                          "subtype (the roles mechanism deals out hidden roles)")

    def _shared_text(self: _Checker, source: str | None, path: str, types: Types,  # type: ignore[misc]
                     params: Mapping[str, C.ParamSpec] | None = None) -> None:
        """Text sent to several agents (an announcement, news, an entry every agent reads) may read no agent's private
        property, not even the actor's own: the engine refuses it."""
        read = self._agent_private_reads(_expressions(source), types, params or {})
        if read:
            self.error(path, f"reads private {', '.join(sorted(read))}, and this text is sent to others than its "
                             "owner: the engine refuses it at run time",
                       "work out what they may learn in game logic and show that (in an action, `\"$shown = "
                       "$actor.cash\"` in `do`, then `{$shown}`; in an event, a property that is not private), or "
                       "send it `to` the owner alone")

    def _agent_private_reads(self: _Checker, expressions: Iterable[Expr], types: Types,  # type: ignore[misc]
                             params: Mapping[str, C.ParamSpec], items: bool = True) -> set[str]:
        """The reads of an agent's private property in ``expressions``: from a typed root (``types``), a chosen
        entity (``$params.<name>.<prop>``) or (``items``) the items of a type (``$sum(player, $it.cash)``), which
        may be guarded to the reader's own."""
        read: set[str] = set()
        for expr in expressions:
            for chain in expr.paths:
                param = params.get(chain[1]) if chain[0] == "params" and len(chain) > 2 else None
                if param is not None:
                    kinds, field = ({param.of} if param.type == "entity" and param.of else set()), chain[2]
                elif len(chain) > 1:
                    kinds, field = types.get(chain[0], set()), chain[1]
                else:
                    continue
                if self._agent_private(kinds, field):
                    read.add("$" + ".".join(chain))
            for function, kind, chain in expr.item_paths if items else ():
                if kind is not None and len(chain) > 1 and self._agent_private({kind}, chain[1]):
                    read.add(f"{chain[1]} of every {kind} (in ${function})")
        return read

    def _fetched_private_reads(self: _Checker, expressions: Iterable[Expr]) -> set[str]:  # type: ignore[misc]
        """The reads of a private property of some agent type from an entity a function fetched
        (``$entity(bo).cash``, ``$first(player).cash``): whose it is shows only at run time."""
        agents = self.c.agent_types()
        return {f"${chain[0]}(…).{chain[1]}" for expr in expressions for chain in expr.call_paths
                if len(chain) > 1 and self._agent_private(agents, chain[1])}

    def _agent_private(self: _Checker, kinds: Iterable[str], field: str) -> bool:  # type: ignore[misc]
        return any(kind in self.c.types and self.c.is_agent(kind) and (prop := self.c.props_of(kind).get(field))
                   is not None and prop.private for kind in kinds)

    def _private_listing(self: _Checker, view: C.ViewSpec, path: str) -> None:  # type: ignore[misc]
        """A view listing every entity of a type by a private property (shown or sorted by) shows each reader
        everyone's; with a `where`, or through a def, it may: reading another agent's is an error at run time."""
        of = str(view.of)
        try:
            expressions = list(compile_template(view.show, "it").expressions)
            if view.sort is not None:
                expressions.append(compile_expr(view.sort))
        except ExprError:
            return  # already reported by the template and expression checks
        shown = self._private_fields(expressions, of)
        if shown and view.where is None:
            self.error(path, f"shows (or sorts by) private {', '.join(shown)} of every {of} to each reader",
                       "add a `where` choosing whose to show (e.g. `$it.id == $actor.id`), or leave the private field "
                       "out")
        elif shown and self.c.is_agent(of):
            self._private_warning(path, ", ".join(shown))
        self._private_via_defs(expressions, path)

    def _private_warning(self: _Checker, path: str, read: str) -> None:  # type: ignore[misc]
        self.warn(path, f"reads private {read}: what an agent is shown or offered may read only its own private "
                        "properties, and reading another agent's there is an error at run time",
                  "guard the read with `$it.id == $actor.id`, or work out what the agent may learn in game logic "
                  "(an action's do, an event) and show that")

    def _private_via_defs(self: _Checker, expressions: Iterable[Expr], path: str) -> None:  # type: ignore[misc]
        """Warn when ``expressions`` call defs (directly or through other defs) that read agents' private properties
        from their arguments or the entities they loop over: whose they read shows only at run time."""
        private = {prop for kind in self.c.agent_types() for prop, spec in self.c.props_of(kind).items()
                   if spec.private}
        pending = [name for expr in expressions for name in (expr.functions | expr.roots) if name in self.c.defs]
        seen: set[str] = set()
        read: set[str] = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            spec = self.c.defs[name]
            try:
                body = compile_expr(spec.expr)
            except ExprError:
                continue  # already reported by the def check
            read |= {f"{chain[1]} (in ${name})" for chain in body.paths
                     if len(chain) > 1 and chain[0] in {*spec.args, "it"} and chain[1] in private}
            pending += [other for other in body.functions | body.roots if other in self.c.defs]
        if read:
            self._private_warning(path, ", ".join(sorted(read)))

    def _private_filter(self: _Checker, where: str | None, of: str, path: str) -> None:  # type: ignore[misc]
        """A choice filtered by another agent's private property reveals it: the tool lists only who passes."""
        if where is None or not self.c.is_agent(of):
            return
        try:
            shown = self._private_fields([compile_expr(where)], of)
        except ExprError:
            return  # already reported by the expression check
        if shown:
            self.error(path, f"filters the choices by private {', '.join(shown)} of other {of} agents: the "
                             "tool's list of choices would reveal it to the actor",
                       "filter by what the actor may know (public properties, its own, a relation or a function such "
                       "as $known_role), or accept any choice and decide in `do`")
        else:
            self._private_via_defs([compile_expr(where)], path)

    def _private_fields(self: _Checker, expressions: Iterable[Expr], of: str) -> list[str]:  # type: ignore[misc]
        """The private properties of ``of`` that ``expressions`` read from ``$it``."""
        specs = self.c.props_of(of)
        return sorted({chain[1] for expr in expressions for chain in expr.paths
                       if len(chain) > 1 and chain[0] == "it" and chain[1] in specs and specs[chain[1]].private})


def _expressions(text: object) -> list[Expr]:
    """The expressions in a template or expression text; none when it has none, or does not compile (the template
    and expression checks report that)."""
    if not isinstance(text, str) or "$" not in text:
        return []
    try:
        return list(compile_template(text, None).expressions) if "{" in text else [compile_expr(text)]
    except ExprError:
        return []
