"""Checking reads of hidden values in what agents are shown (mixed into the contract checker).

A property declared `private` is hidden from every agent but its owner: an agent owns its own; the world's and any other
entity's have no owner unless a `where` picks the items by the reader (see world/hidden.py).
The engine refuses reading a hidden value in what an agent is shown or offered; these checks report such reads before
a run hits them.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .. import contract as C
from ..actions.book import announces, stage_actions
from ..actions.reads import inspect_rule
from ..expr import Expr, ExprError, compile_expr
from ..expr.template import compile_template
from ..world.hidden import reveals

if TYPE_CHECKING:
    from . import _Checker
    from .roots import Types

__all__ = ["PrivacyChecks"]


class PrivacyChecks:
    """Hidden values in views, tools, outcomes, announcements, news and `who` (mixed into the contract checker)."""

    def _private_action(self: _Checker, spec: C.ActionSpec, types: Types, path: str) -> None:  # type: ignore[misc]
        """An action's announcement is sent to everyone; its outcome, `why`s and parameters' bounds, defaults and
        choices are what its actor is shown or offered, where a value hidden from it is refused (another agent's
        private property whenever the actor chooses another agent). A requirement that reads one decides by what the
        actor cannot know."""
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
            self._actor_text(text, f"{path}.{key}", spec.params)
        for index, condition in enumerate(spec.when):
            expressions = _expressions(condition.expr)
            read = self._hidden_reads(expressions, {}, spec.params) | self._fetched_reads(expressions)
            if read:
                self.warn(f"{path}.when[{index}]", f"decides by private {', '.join(sorted(read))}, which the actor "
                                                   "cannot see: it cannot know when the action is allowed, and each "
                                                   "refused call spends its action",
                          "decide by what the actor may know; if the refusal is a deliberate guess at a hidden value, "
                          "test it in `do` (`{\"if\": ..., \"then\": [{\"fail\": ...}]}`)")

    def _actor_text(self: _Checker, text: object, path: str,  # type: ignore[misc]
                    params: Mapping[str, C.ParamSpec]) -> None:
        """What an action's actor is shown or offered (its outcome, a `fail`, a bound, a default, a `why`) is refused
        a value hidden from it."""
        expressions = _expressions(text)
        read = self._hidden_reads(expressions, {}, params, items=False)
        if read:
            self.error(path, f"reads private {', '.join(sorted(read))}: what the actor is shown or offered is refused "
                             "a value hidden from it",
                       "work out what the actor may learn in `do` (`\"$seen = $params.target.role\"`) and show "
                       "`{$seen}`, or read only what it may know")
        elif fetched := self._fetched_reads(expressions):
            self.warn(path, f"reads private {', '.join(sorted(fetched))}: what the actor is shown or offered is "
                            "refused it at run time unless it is the actor's own",
                      "read the actor's own through `$actor`, or work out what it may learn in game logic and show "
                      "that")

    def _private_who(self: _Checker, stage: C.StageSpec, path: str) -> None:  # type: ignore[misc]
        """A `who` that reads a hidden value, in a stage whose actions are announced: everyone learns whom it woke."""
        if stage.who is None or not announces(self.c, stage):
            return
        expressions = _expressions(stage.who)
        read = self._hidden_reads(expressions, {"it": set(self.agents)}, {}) | self._fetched_reads(expressions)
        if read:
            self.error(path, f"reads private {', '.join(sorted(read))}, and every agent learns who acts in "
                             f"{stage.name} (its actions are announced): the engine refuses it at run time",
                       "wake by what is not private, or make the stage's actions `private` so nobody learns who "
                       "acted")

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
        """Text sent to several agents (an announcement, news, an entry every agent reads) may read no private
        property, not even the actor's own: the engine refuses it."""
        read = self._hidden_reads(_expressions(source), types, params or {})
        if read:
            self.error(path, f"reads private {', '.join(sorted(read))}, and this text is sent to more than one agent: "
                             "the engine refuses it at run time",
                       "work out what they may learn in game logic and show that (in an action, `\"$shown = "
                       "$actor.cash\"` in `do`, then `{$shown}`; in an event, a property that is not private), or "
                       "send it `to` the owner alone")

    def _hidden_reads(self: _Checker, expressions: Iterable[Expr], types: Types,  # type: ignore[misc]
                      params: Mapping[str, C.ParamSpec], items: bool = True) -> set[str]:
        """The reads of a private property in ``expressions``: of the world, from a typed root (``types``), a chosen
        entity (``$params.<name>.<prop>``) or (``items``) the items of a type (``$sum(player, $it.cash)``), which may
        be guarded to the reader's own."""
        world = {name for name, spec in self.c.world.items() if spec.private}
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
                if self._private(kinds, field) or (chain[0] == "world" and field in world):
                    read.add("$" + ".".join(chain))
            for function, kind, chain in expr.item_paths if items else ():
                if kind is not None and len(chain) > 1 and self._private({kind}, chain[1]):
                    read.add(f"{chain[1]} of every {kind} (in ${function})")
        return read

    def _fetched_reads(self: _Checker, expressions: Iterable[Expr]) -> set[str]:  # type: ignore[misc]
        """The reads of a property some type keeps private from an entity a function fetched (``$entity(bo).cash``,
        ``$first(player).cash``): whose it is shows only at run time."""
        return {f"${chain[0]}(…).{chain[1]}" for expr in expressions for chain in expr.call_paths
                if len(chain) > 1 and self._private(self.c.types, chain[1])}

    def _private(self: _Checker, kinds: Iterable[str], field: str) -> bool:  # type: ignore[misc]
        return any(kind in self.c.types and (prop := self.c.props_of(kind).get(field)) is not None and prop.private
                   for kind in kinds)

    def _private_view(self: _Checker, view: C.ViewSpec, path: str) -> None:  # type: ignore[misc]
        """A view is what its reader is shown: a private world property, or a private property of every entity of a
        type (``$count(card, $it.face == ace)``), read anywhere in it is refused (its listed items' own are checked
        by :meth:`_private_listing`)."""
        texts = {"when": view.when, "title": view.title, "show": view.show, "sort": view.sort, "where": view.where,
                 "attach": view.attach}
        if view.of is not None and view.of not in self.c.types and view.of not in self.c.records:
            texts["of"] = view.of
        for key, text in texts.items():
            implicit = "it" if key == "show" and view.of is not None else "actor" if key in ("show", "title") else None
            read = self._hidden_reads(_expressions(text, implicit), {}, {})
            if read:
                self.error(f"{path}.{key}", f"reads private {', '.join(sorted(read))}, which its readers may not see: "
                                            "the engine refuses it at run time",
                           "work out what agents may learn in game logic (an action's do, an event) and show that")

    def _private_listing(self: _Checker, view: C.ViewSpec, path: str) -> None:  # type: ignore[misc]
        """A view listing the entities of a type by a private property (shown, attached, sorted or filtered by) shows it
        to each reader, unless its `where` picks the items the reader owns (see world/hidden.py). An agent's own private
        property may be shown to it: a `where` may guard to that, so over agents this is a warning (reading another
        agent's is an error at run time)."""
        of = str(view.of)
        try:
            expressions = list(compile_template(view.show, "it").expressions)
            expressions += [compile_expr(text) for text in (view.sort, view.attach) if text is not None]
            where = compile_expr(view.where) if view.where is not None else None
        except ExprError:
            return  # already reported by the template and expression checks
        read = [*expressions, *([where] if where is not None else [])]
        shown = [] if where is not None and reveals(self.c, where, of) else self._private_fields(read, of)
        if shown and (where is None or not self.c.is_agent(of)):
            self.error(path, f"shows (or sorts or filters by) private {', '.join(shown)} of every {of} to each reader",
                       "pick the items the reader owns in `where` (e.g. `$it.owner == $actor.id`, or `$it.id == "
                       "$actor.id` for an agent's own), or leave the private field out")
        elif shown:
            self._private_warning(path, ", ".join(shown))
        self._private_via_defs(read, path)  # a def is called without the reveal: it reads as for anyone

    def _private_warning(self: _Checker, path: str, read: str) -> None:  # type: ignore[misc]
        self.warn(path, f"reads private {read}: what an agent is shown or offered may read only its own private "
                        "properties, and reading another agent's there is an error at run time",
                  "guard the read with `$it.id == $actor.id`, or work out what the agent may learn in game logic "
                  "(an action's do, an event) and show that")

    def _private_via_defs(self: _Checker, expressions: Iterable[Expr], path: str) -> None:  # type: ignore[misc]
        """Warn when ``expressions`` call defs (directly or through other defs) that read private properties from
        their arguments or the entities they loop over: whose they read shows only at run time."""
        private = {prop for kind in self.c.types for prop, spec in self.c.props_of(kind).items() if spec.private}
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
        """A choice filtered by a private property reveals it — the tool lists only the entities that pass — unless
        the `where` picks the items the actor owns (see world/hidden.py)."""
        if where is None:
            return
        try:
            compiled = compile_expr(where)
        except ExprError:
            return  # already reported by the expression check
        shown = [] if reveals(self.c, compiled, of) else self._private_fields([compiled], of)
        if shown:
            self.error(path, f"filters the choices by private {', '.join(shown)} of {of}: the tool's list of choices "
                             "would reveal it to the actor",
                       "filter by what the actor may know (public properties, its own, a relation or a function such "
                       "as $known_role), pick the items the actor owns (`$it.owner == $actor.id`), or accept any "
                       "choice and decide in `do`")
        elif self.c.is_agent(of):  # a def may read another agent's; the run refuses any other hidden read loudly
            self._private_via_defs([compiled], path)

    def _private_fields(self: _Checker, expressions: Iterable[Expr], of: str) -> list[str]:  # type: ignore[misc]
        """The private properties of ``of`` that ``expressions`` read from ``$it``."""
        specs = self.c.props_of(of)
        return sorted({chain[1] for expr in expressions for chain in expr.paths
                       if len(chain) > 1 and chain[0] == "it" and chain[1] in specs and specs[chain[1]].private})


def _expressions(text: object, implicit: str | None = None) -> list[Expr]:
    """The expressions in a template or expression text (a template's bare names read ``implicit``); none when it has
    none, or does not compile (the template and expression checks report that)."""
    if not isinstance(text, str) or ("$" not in text and (implicit is None or "{" not in text)):
        return []
    try:
        return list(compile_template(text, implicit).expressions) if "{" in text else [compile_expr(text)]
    except ExprError:
        return []
