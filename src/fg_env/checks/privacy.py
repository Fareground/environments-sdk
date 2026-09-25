"""Checking reads of hidden values in what agents are shown (a part of the contract checker).

A property declared `private` is hidden from every agent but the entity itself, its owner (the agent its type's
`owner` property names) and the agent types its `private` lists; the world's have no owner (see expr/hidden.py).
The engine refuses reading a hidden value in what an agent is shown or offered; these checks report such reads before
a run hits them.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .. import contract as C
from ..actions.book import announces, stage_actions
from ..expr import Expr, ExprError, compile_expr, is_expr
from ..expr.compile import and_terms, call_roots
from ..expr.hidden import Hidden, readers
from ..expr.template import compile_template
from ..information.reads import inspect_rule
from ..world.parts import private_metrics
from .core import Checker

if TYPE_CHECKING:
    from .roots import Types

__all__ = ["PrivacyChecks"]


class PrivacyChecks(Checker):
    """Hidden values in views, tools, outcomes, announcements, news and `who` (a part of the contract checker)."""

    #: The agent types that read what is being checked (a view's readers, an action's actors): a property whose
    #: `private` lists every one of them is theirs to read. Empty for text sent to several and anything else.
    readers: frozenset[str] = frozenset()

    @contextmanager
    def _reading(self, kinds: Iterable[str]) -> Iterator[None]:
        """Check what agents of ``kinds`` read."""
        previous, self.readers = self.readers, frozenset(kinds)
        try:
            yield
        finally:
            self.readers = previous

    def _readable(self, spec: C.PropSpec) -> bool:
        """Whether every reader being checked is of a type ``spec``'s `private` lists."""
        allowed = readers(spec.private)
        return bool(allowed and self.readers) and all(set(self.c.lineage(kind)) & allowed for kind in self.readers)

    def _private_action(self, spec: C.ActionSpec, types: Types, path: str) -> None:
        """An action's announcement is sent to everyone; its outcome, `why`s, parameters' bounds, defaults and
        choices, and whether it ends the turn (`terminal`) are what its actor is shown or offered, where a value hidden
        from it is refused (another agent's private property whenever the actor chooses another agent). A requirement
        that reads one decides by what the actor cannot know."""
        if isinstance(spec.announce, str):
            self._shared_text(spec.announce, f"{path}.announce", types, spec.params)
            worked_out = sorted({chain[1] for expr in _expressions(spec.announce) for chain in expr.paths
                                 if chain[0] == "params" and len(chain) > 1 and chain[1] in spec.params
                                 and is_expr(spec.params[chain[1]].default)})
            if worked_out:
                self.error(f"{path}.announce",
                           f"reads {', '.join('$params.' + name for name in worked_out)}, whose default is worked out "
                           "as the actor sees the world, and this text is sent to more than one agent",
                           "give the argument a plain default (or none), or work out what they may learn in `do` "
                           "(`\"$shown = $params.<name>\"`) and show `{$shown}`")
        with self._reading(types.get("actor", ())):
            self._actor_texts(spec, path)

    def _actor_texts(self, spec: C.ActionSpec, path: str) -> None:
        texts = {"outcome": spec.outcome, "terminal": spec.terminal}
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
            read = self._hidden_reads(expressions, {}, spec.params) | self._fetched_reads(expressions) \
                | self._log_reads(expressions)
            if read:
                self.warn(f"{path}.when[{index}]", f"decides by private {', '.join(sorted(read))}, which the actor "
                                                   "cannot see: it cannot know when the action is allowed, and each "
                                                   "refused call spends its action",
                          "decide by what the actor may know; if the refusal is a deliberate guess at a hidden value, "
                          "test it in `do` (`{\"if\": ..., \"then\": [{\"fail\": ...}]}`)")

    def _actor_text(self, text: object, path: str, params: Mapping[str, C.ParamSpec]) -> None:
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
            self.error(path, f"reads private {', '.join(sorted(fetched))}: what the actor is shown or offered is "
                             "refused it at run time for every actor but its owner",
                       "read the actor's own through `$actor`, or work out what it may learn in game logic and show "
                       "that")

    def _private_who(self, stage: C.StageSpec, path: str) -> None:
        """A `who` that reads a hidden value, in a stage whose actions are announced: everyone learns whom it woke."""
        if stage.who is None or not announces(self.c, stage):
            return
        expressions = _expressions(stage.who)
        read = self._hidden_reads(expressions, {"it": set(self.agents)}, {}) | self._fetched_reads(expressions)
        if read:
            self.error(path, f"reads private {', '.join(sorted(read))}, and every agent learns who acts in "
                             f"{stage.name} (its actions are announced): the engine refuses it at run time",
                       "wake by what is not private, or give the stage's actions `announce: false` so nobody learns "
                       "who acted")

    def _private_stage_when(self, stage: C.StageSpec, path: str) -> None:
        """A stage's `when`, `until` or `passes` that reads a hidden value: every agent learns something of it from
        whether the stage was held (the stage's name opens every update in it) or how many passes it played (when the
        stage wakes everyone, or announces what its agents do)."""
        self._private_gate(stage.when, path, f"whether {stage.name} was held (it names the stage it plays)")
        if stage.who is not None and not announces(self.c, stage):
            return  # only the agents it wakes see its passes, and nobody else learns what they did
        base, passes = path.rsplit(".", 1)[0], f"how many passes {stage.name} played (each wakes them again)"
        self._private_gate(stage.until, f"{base}.until", passes)
        if isinstance(stage.passes, str):
            self._private_gate(stage.passes, f"{base}.passes", passes)

    def _private_gate(self, condition: object, path: str, learns: str, types: Types | None = None,
                      params: Mapping[str, C.ParamSpec] | None = None) -> None:
        """A condition that reads a hidden value and decides something every agent sees (a stage held, news sent to
        everyone): every agent learns a bit of the value from ``learns``. Warned alike wherever it is written."""
        expressions = _expressions(condition)
        read = self._hidden_reads(expressions, types or {}, params or {}) | self._fetched_reads(expressions) \
            | self._log_reads(expressions)
        if read:
            self.warn(path, f"reads private {', '.join(sorted(read))}, and every agent learns {learns}",
                      "decide it by what is not private, or keep a public property that says what everyone may know "
                      "(\"$world.night = ...\" in game logic) and read that")

    def _sealed_announced(self, stage: C.StageSpec, path: str) -> None:
        """A simultaneous stage announces each sealed choice to everyone by its action's name as it commits (unless
        the action has `announce: false` or says what to announce): with more than one to choose from, each agent's
        choice — a secret ballot's vote — is public."""
        if stage.turns != "simultaneous":
            return
        for kind in self.c.agent_types():
            named = [name for name in stage_actions(self.c, stage, kind)
                     if self.c.actions[name].announce is None]
            if len(named) > 1:
                self.warn(path, f"announces each sealed choice to everyone by name as it commits (\"Ann: "
                                f"{named[0].replace('_', ' ')}.\"), so which of {', '.join(named)} each agent chose "
                                "is public",
                          "if the choice is secret (a ballot), give those actions `announce: false` and announce only "
                          f"the outcome (an `emit` in an event on 'stage.{stage.name}.end'); if it is meant to be "
                          "public, give them an `announce` text")
                return

    def _secret_subtypes(self) -> None:
        """An agent subtype with private actions of its own, of a type agents may inspect: inspect names each agent's
        type, so everyone can tell who is one."""
        warned: set[str] = set()
        for name, spec in self.c.actions.items():
            for kind in [spec.by] if isinstance(spec.by, str) else spec.by:
                if not spec.silent or kind in warned or kind not in self.c.types or not self.c.is_agent(kind):
                    continue
                root = self.c.lineage(kind)[0]
                if root == kind or inspect_rule(self.c, kind) is False:
                    continue
                warned.add(kind)
                self.warn(f"types.{kind}", f"inspect names each agent's type, so everyone can tell who is a {kind}, "
                                           f"though nobody else learns of its action {name}",
                          f"if being a {kind} is a secret, keep it in a private property of {root} instead of a "
                          "subtype (the roles mechanism deals out hidden roles)")

    def _unshown_private(self) -> None:
        """An agent's own fixed private trait that nothing it reads ever shows — no view, brief, outcome, tool or
        policy, and no inspect of itself — is something its agent can never learn: a haggler that never sees its own
        value. Only properties the author declared on the type itself (a mechanism's bookkeeping is its business) that
        no rule writes (a tally the rules keep, such as attacks this night, is theirs), of types no coded policy plays
        by default (a crowd's hidden traits are its code's)."""
        if any(spec.inspect for spec in self.c.types.values()):
            return  # an agent may inspect itself, private properties and all
        written = (self.c._source or {}).get("types") if isinstance(self.c._source, dict) else None
        corpus = json.dumps({"views": self.c.model_dump(by_alias=True).get("views"),
                             "brief": self.c.brief.model_dump(), "defs": {n: d.expr for n, d in self.c.defs.items()},
                             "stages": [stage.brief for stage in self.c.stage_list()],
                             "entities": {n: e.brief for n, e in self.c.entities.items()},
                             "actions": {n: a.model_dump(by_alias=True, exclude={"do"}) for n, a in
                                         self.c.actions.items()},
                             "policies": {kind: self.c.policies_of(kind) for kind in self.c.agent_types()},
                             "said": _said(self.c.model_dump(by_alias=True))}, default=str)
        rules = json.dumps(self.c.model_dump(by_alias=True, exclude={"views", "brief", "outputs"}), default=str)
        for kind in self.c.agent_types():
            if any(self.c.types[name].policy for name in self.c.lineage(kind)):
                continue  # coded by default: its hidden traits are the code's to read
            declared = ((written or {}).get(kind) or {}).get("props") or {} if isinstance(written, dict) else {}
            for prop, spec in self.c.types[kind].props.items():
                if spec.private and prop in declared and not re.search(rf"\b{re.escape(prop)}\b", corpus) \
                        and not re.search(rf"\.{re.escape(prop)}\s*(?:\[[^\]]*\]\s*)*[-+*/]?=(?!=)", rules):
                    self.warn(f"types.{kind}.props.{prop}",
                              f"is private to each {kind}, but nothing a {kind} reads shows it (no view, brief, "
                              "outcome or tool), so its agent can never learn it",
                              f"show it to its owner, e.g. a view {{\"for\": \"{kind}\", \"show\": \"Your "
                              f"{prop}: {{{prop}}}\"}}, or say it in brief.roles.{kind}; if agents need not know it, "
                              "it need not be private")

    def _shared_text(self, source: str | None, path: str, types: Types,
                     params: Mapping[str, C.ParamSpec] | None = None) -> None:
        """Text sent to several agents (an announcement, news, an entry every agent reads) may read no private
        property, not even the actor's own: the engine refuses it."""
        expressions = _expressions(source)
        read = self._hidden_reads(expressions, types, params or {}) | self._fetched_reads(expressions)
        if read:
            self.error(path, f"reads private {', '.join(sorted(read))}, and this text is sent to more than one agent: "
                             "the engine refuses it at run time",
                       "work out what they may learn in game logic and show that (in an action, `\"$shown = "
                       "$actor.cash\"` in `do`, then `{$shown}`; in an event, a property that is not private), or "
                       "send it `to` the owner alone")

    def _said_to(self, source: str | None, to: object, path: str, types: Types) -> None:
        """Text sent `to` others than the actor may not read the actor's own private properties (those no agent type
        reads besides it): only its one reader's may show."""
        if isinstance(to, str) and to.strip() in ("$actor", "$actor.id"):
            return
        actor = types.get("actor", set())
        read = sorted({"$" + ".".join(chain) for expr in _expressions(source) for chain in expr.paths
                       if len(chain) > 1 and chain[0] == "actor"
                       and any(kind in self.c.types and (spec := self.c.props_of(kind).get(chain[1])) is not None
                               and spec.private is True for kind in actor)})
        if read:
            self.error(path, f"reads the actor's private {', '.join(read)} in text sent to {to}: only its one reader's "
                             "private properties may show, and the engine refuses it at run time",
                       "send it `to` the actor, or work out what the recipient may learn in `do` (`\"$shown = "
                       "$actor.card\"`) and show `{$shown}`")

    def _hidden_reads(self, expressions: Iterable[Expr], types: Types,
                      params: Mapping[str, C.ParamSpec], items: bool = True) -> set[str]:
        """The reads of a private property in ``expressions``: of the world, from a typed root (``types``), a chosen
        entity (``$params.<name>.<prop>``) or (``items``) the items of a type (``$sum(player, $it.cash)``), which may
        be guarded to the reader's own."""
        world = {name for name, spec in self.c.world.items() if spec.private and not self._readable(spec)}
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
                if self._private(kinds, field) or (chain[0] == "world" and field in world) \
                        or (chain[0] in ("outputs", "series") and field in self._private_outputs):
                    read.add("$" + ".".join(chain))
            for function, kind, chain in expr.item_paths if items else ():
                if kind is not None and len(chain) > 1 and self._private({kind}, chain[1]):
                    read.add(f"{chain[1]} of every {kind} (in ${function})")
        return read

    def _fetched_reads(self, expressions: Iterable[Expr]) -> set[str]:
        """The reads of a property some type keeps private from an entity a function fetched (``$entity(bo).cash``,
        ``$first(player).cash``): whose it is shows only at run time."""
        return {f"${chain[0]}(…).{chain[1]}" for expr in expressions for chain in expr.call_paths
                if len(chain) > 1 and self._private(self.c.types, chain[1])}

    def _log_reads(self, expressions: Iterable[Expr]) -> set[str]:
        """The reads, in game logic, of record entries or events some agent may not see: a record that may hold such
        an entry, events of a kind that may be kept from some (see expr/hidden.py)."""
        hidden = self._hidden
        read: set[str] = set()
        for expr in expressions:
            for function, symbol in expr.calls:
                if function == "records" and (symbol in hidden.records or (symbol is None and hidden.records)):
                    read.add(f"$records({symbol or '…'}) (entries not every agent sees)")
                elif function == "events" and hidden.events_hide(symbol):
                    read.add(f"$events({symbol or '…'}) (events not every agent sees)")
        return read

    @property
    def _private_outputs(self) -> frozenset[str]:
        """The series outputs worked out from private properties: an agent may not be shown one."""
        found = getattr(self, "_private_outputs_found", None)
        if found is None:
            found = self._private_outputs_found = private_metrics(self.c, self._hidden.names)
        return found

    @property
    def _hidden(self) -> Hidden:
        hidden = getattr(self, "_hidden_model", None)
        if hidden is None:
            hidden = self._hidden_model = Hidden(self.c)
        return hidden

    def _private(self, kinds: Iterable[str], field: str) -> bool:
        return any(kind in self.c.types and (prop := self.c.props_of(kind).get(field)) is not None and prop.private
                   and not self._readable(prop) for kind in kinds)

    def _private_view(self, view: C.ViewSpec, path: str) -> None:
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

    def _private_listing(self, view: C.ViewSpec, path: str) -> None:
        """A view listing the entities of a type by a private property (shown, attached, sorted or filtered by) shows
        each reader only the items it may read: the entities it owns, which its `where` picks (see expr/hidden.py).
        Without a `where` it would show every reader every item's; over a type with no owner, only the entity itself
        and the listed types read them, so nobody else is ever shown one."""
        of = str(view.of)
        try:
            expressions = list(compile_template(view.show, "it").expressions)
            expressions += [compile_expr(text) for text in (view.sort, view.attach) if text is not None]
            where = compile_expr(view.where) if view.where is not None else None
        except ExprError:
            return  # already reported by the template and expression checks
        read = [*expressions, *([where] if where is not None else [])]
        shown = self._private_fields(read, of)
        if shown and not self._ownable(of):
            self.error(path, f"shows (or sorts or filters by) private {', '.join(shown)} of {of}, which no reader "
                             f"owns: an entity's private properties are read only by itself, its owner and the types "
                             f"its `private` lists",
                       self._owner_fix(of, where))
        elif shown and where is None:
            self.error(path, f"shows (or sorts or filters by) private {', '.join(shown)} of every {of} to each reader",
                       "pick the items the reader owns in `where` (`$it.id == $actor.id` for an agent's own, "
                       "`$it.owner == $actor.id` for a type whose `owner` is owner), or leave the private field out")
        elif early := self._unguarded(str(view.where), of):
            self.error(f"{path.removesuffix('.show')}.where",
                       f"reads private {', '.join(early)} of {of} before picking the reader's own: for an item the "
                       "reader does not own, that read is an error at run time",
                       "test ownership first, as the first term of an `and` (`$it.id == $actor.id and ...`, or "
                       "`$it.<its owner property> == $actor.id and ...`)")
        self._private_via_defs(read, path)  # a def may read another entity's: whose shows only at run time

    def _unguarded(self, where: str, of: str, reader: str = "actor") -> list[str]:
        """The private properties of ``of`` a view's ``where`` (or an inspect rule) reads before a term that picks the
        reader's own items (``$it.id`` or ``$it.<owner>`` against ``$actor``, or ``reader``): the terms of an `and`
        are read in order and stop at the first false one, so what is read before that term (or in a `where` with
        none) is read for items the reader does not own."""
        ownership = {"id", self.c.owner_of(of)}
        read: list[str] = []
        for term in and_terms(where):
            expr = compile_expr(term)
            private = self._private_fields([expr], of)
            if not private and reader in expr.roots \
                    and any(len(chain) == 2 and chain[0] == "it" and chain[1] in ownership for chain in expr.paths):
                return read
            read += [name for name in private if name not in read]
        return read

    def _private_inspect(self, kind: str, rule: str, path: str) -> None:
        """An inspect rule is read for each reader over every entity of ``kind`` it might inspect: a private property
        of the entity read before picking the reader's own, another's fetched, a private world property or an output
        worked out from private ones is refused at run time, the first time an agent's tools are listed."""
        try:
            compiled = compile_expr(rule)
        except ExprError:
            return  # reported by the condition check
        with self._reading(self.agents):
            read = [f"$it.{name}" for name in self._unguarded(rule, kind, "viewer")]
            read += sorted(self._hidden_reads([compiled], {}, {}, items=False) | self._fetched_reads([compiled]))
        if read:
            self.error(path, f"reads private {', '.join(read)} for entities the reader does not own: the engine "
                             "refuses it at run time", "decide by what every reader may know, or test ownership first "
                                                       "(`$it.<owner> == $viewer.id and ...`)")

    def _ownable(self, kind: str) -> bool:
        """Whether some agent owns each entity of ``kind``: an agent owns itself; any other entity has an owner when
        its type names the property holding it (`owner`)."""
        return self.c.is_agent(kind) or self.c.owner_of(kind) is not None

    def _owner_fix(self, kind: str, where: Expr | None) -> str:
        """How to let a reader read the private properties of the ``kind`` entities it owns: name the property that
        holds each one's owner (the one ``where`` picks them by, when it names one)."""
        specs = self.c.props_of(kind)
        named = [chain[1] for chain in (where.paths if where is not None else ())
                 if len(chain) > 1 and chain[0] == "it" and chain[1] in specs and not specs[chain[1]].private]
        prop = named[0] if named else "<the property holding the owner's id>"
        return (f"if each {kind} belongs to an agent, say which property holds its owner's id (`\"owner\": "
                f"\"{prop}\"` in types.{kind}) and pick the reader's in `where`; to show it to a kind of agent, list "
                "that type in the property's `private`; otherwise leave it out")

    def _private_warning(self, path: str, read: str) -> None:
        self.warn(path, f"reads private {read}: what an agent is shown or offered may read only its own private "
                        "properties, and reading another agent's there is an error at run time",
                  "guard the read with `$it.id == $actor.id`, or work out what the agent may learn in game logic "
                  "(an action's do, an event) and show that")

    def _private_via_defs(self, expressions: Iterable[Expr], path: str) -> None:
        """Warn when ``expressions`` call defs (directly or through other defs) that read private properties from
        their arguments or the entities they loop over: whose they read shows only at run time. An argument every
        such call passes the reader itself (``$actor``, or an argument that is the reader in turn) is the reader's
        own: reading its private properties is allowed."""
        private = {prop for kind in self.c.types for prop, spec in self.c.props_of(kind).items() if spec.private}
        defs = self.c.expr_defs()
        pending: list[tuple[str, frozenset[str]]] = []
        for expr in expressions:
            pending += _def_calls(defs, expr, frozenset({"actor"}))
        seen: set[tuple[str, frozenset[str]]] = set()
        read: set[str] = set()
        while pending:
            call = pending.pop()
            if call in seen:
                continue
            seen.add(call)
            name, reader = call
            spec = defs[name]
            try:
                body = compile_expr(spec.expr or "")
            except ExprError:
                continue  # already reported by the def check
            read |= {f"{chain[1]} (in ${name})" for chain in body.paths
                     if len(chain) > 1 and chain[0] in {*spec.args, "it"} - reader and chain[1] in private}
            pending += _def_calls(defs, body, reader)
        if read:
            self._private_warning(path, ", ".join(sorted(read)))

    def _private_filter(self, where: str | None, of: str, path: str) -> None:
        """A choice filtered by a private property reveals it — the tool lists only the entities that pass — unless
        the entities have an owner (see expr/hidden.py), whose own the `where` may test: reading anyone else's is then
        refused at run time."""
        if where is None:
            return
        try:
            compiled = compile_expr(where)
        except ExprError:
            return  # already reported by the expression check
        shown = self._private_fields([compiled], of)
        if shown and self.c.owner_of(of) is None:
            self.error(path, f"filters the choices by private {', '.join(shown)} of {of}: the tool's list of choices "
                             "would reveal it to the actor",
                       "filter by what the actor may know (public properties, its own, a relation or a function such "
                       "as $known_role), or accept any choice and decide in `do`"
                       + ("" if self.c.is_agent(of) else "; or " + self._owner_fix(of, compiled)))
        else:
            self._private_via_defs([compiled], path)

    def _private_fields(self, expressions: Iterable[Expr], of: str) -> list[str]:
        """The private properties of ``of`` that ``expressions`` read from ``$it``."""
        specs = self.c.props_of(of)
        return sorted({chain[1] for expr in expressions for chain in expr.paths
                       if len(chain) > 1 and chain[0] == "it" and chain[1] in specs and specs[chain[1]].private
                       and not self._readable(specs[chain[1]])})


def _said(data: object) -> list[str]:
    """Every text the rules post or send (a `post`'s `text`, an `emit`'s `say`): what agents may read of them."""
    if isinstance(data, dict):
        own = [data[key] for key in ("text", "say") if isinstance(data.get(key), str) and ("post" in data or
                                                                                           "emit" in data)]
        return own + [text for value in data.values() for text in _said(value)]
    if isinstance(data, list):
        return [text for value in data for text in _said(value)]
    return []


def _def_calls(defs: Mapping[str, C.DefSpec], expr: Expr, reader: frozenset[str]) -> list[tuple[str, frozenset[str]]]:
    """The defs ``expr`` calls or reads, each with its arguments that are passed the reader: a root in ``reader``."""
    calls = [(name, frozenset(defs[name].args[n] for n, root in enumerate(roots)
                              if root in reader and n < len(defs[name].args)))
             for name, roots in call_roots(expr.source) if name in defs]
    return calls + [(name, frozenset()) for name in expr.roots if name in defs]


def _expressions(text: object, implicit: str | None = None) -> list[Expr]:
    """The expressions in a template or expression text (a template's bare names read ``implicit``); none when it has
    none, or does not compile (the template and expression checks report that)."""
    if not isinstance(text, str) or ("$" not in text and (implicit is None or "{" not in text)):
        return []
    try:
        return list(compile_template(text, implicit).expressions) if "{" in text else [compile_expr(text)]
    except ExprError:
        return []
