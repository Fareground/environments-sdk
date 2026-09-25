"""Checking reads of hidden values in what agents are shown (a part of the contract checker).

A property declared `private` is hidden from every agent but the entity itself, its owner (the agent its type's
`owner` property names) and the agent types its `private` lists; the world's have no owner (see expr/hidden.py).
The engine refuses reading a hidden value in what an agent is shown or offered; these checks report such reads before
a run hits them.
"""
from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .. import contract as C
from ..actions.book import stage_actions
from ..expr import FUNCTIONS, Expr, ExprError, compile_expr, is_expr
from ..expr.compile import FUNC_PREFIX, ROOT_PREFIX, call_roots, syntax_tree
from ..expr.hidden import Hidden, readers
from ..expr.template import compile_template
from ..information.reads import inspect_rule
from ..world.parts import private_defs, private_metrics
from .core import Checker

if TYPE_CHECKING:
    from .roots import Types

__all__ = ["PrivacyChecks"]


#: The items of a per-item function worked out by an expression: entities of any type.
_ITEMS_OF_ANY = "*"
#: The functions whose result is some of the items they go over (so its items are of their type).
_KEEPS_ITEMS = frozenset({"filter", "sort", "top", "shuffle", "sample", "first", "last", "choice", "best", "worst"})
#: How to decide something every agent sees without reading what they may not know.
_PUBLIC_INSTEAD = ("decide it by what is not private, or keep a public property that says what everyone may know "
                   "(\"$world.night = ...\" in game logic) and read that")


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
        """What an action's fields show is checked by who reads each (:mod:`.field_reads`). Beyond that: its
        announcement may not repeat an argument whose default is worked out as the actor sees the world, and a
        requirement that reads a hidden value decides by what the actor cannot know."""
        if isinstance(spec.announce, str):
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
            for index, condition in enumerate(spec.when):
                expressions = _expressions(condition.expr)
                read = self._hidden_reads(expressions, {}, spec.params) | self._fetched_reads(expressions) \
                    | self._log_reads(expressions)
                if read:
                    self.warn(f"{path}.when[{index}]", f"decides by private {', '.join(sorted(read))}, which the "
                                                       "actor cannot see: it cannot know when the action is allowed, "
                                                       "and each refused call spends its action",
                              "decide by what the actor may know; if the refusal is a deliberate guess at a hidden "
                              "value, test it in `do` (`{\"if\": ..., \"then\": [{\"fail\": ...}]}`)")

    def _private_gate(self, condition: object, path: str, learns: str, types: Types | None = None,
                      params: Mapping[str, C.ParamSpec] | None = None) -> None:
        """A condition that reads a hidden value and decides news every agent is sent: every agent learns a bit of the
        value from ``learns``. Warned alike wherever it is written: the rules may mean to reveal it (a showdown)."""
        read = self._gate_reads(condition, types or {}, params or {})
        if read:
            self.warn(path, f"reads private {', '.join(sorted(read))}, and every agent learns {learns}",
                      _PUBLIC_INSTEAD)

    def _gate_reads(self, condition: object, types: Types, params: Mapping[str, C.ParamSpec]) -> set[str]:
        """The hidden values ``condition`` reads."""
        expressions = _expressions(condition)
        return self._hidden_reads(expressions, types, params) | self._fetched_reads(expressions) \
            | self._log_reads(expressions)

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
                who = next((key if spec.name is None else spec.name for key, spec in self.c.named_entities().items()
                            if spec.type == kind), f"a {kind}")
                self.warn(path, f"announces each sealed choice to everyone by name as it commits (\"{who}: "
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
    def _private_defs(self) -> frozenset[str]:
        """The defs worked out from private properties: text sent to several may not read one."""
        found = getattr(self, "_private_defs_found", None)
        if found is None:
            found = self._private_defs_found = private_defs(self.c, self._hidden.names)
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

    def _private_listing(self, view: C.ViewSpec, path: str) -> None:
        """A view listing the entities of a type shows each reader, of each item, only what it may read: an item's
        private properties only where the item is the reader's own (see :meth:`_item_reads`) — for every item its
        `where` passes when that picks the reader's own, and inside a test of it anywhere. Over a type with no owner,
        only the entity itself and the listed types read them, so nobody else is ever shown one."""
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
            return
        early = self._item_reads(view.where, of)
        if early:
            self.error(f"{path.removesuffix('.show')}.where",
                       f"reads private {', '.join(early)} of {of} before picking the reader's own: for an item the "
                       "reader does not own, that read is an error at run time",
                       "test ownership first, as the first term of an `and` (`$it.id == $actor.id and ...`, or "
                       "`$it.<its owner property> == $actor.id and ...`), or inside an `if` on it")
        picked = where is not None and self._picks_own(view.where, of)
        listed = [expr.source for expr in expressions]
        late = [name for source in listed for name in self._item_reads(source, of, guarded=picked)]
        if late and not early:
            self.error(path, f"shows (or sorts or filters by) private {', '.join(dict.fromkeys(late))} of every {of} "
                             "to each reader",
                       "pick the items the reader owns in `where` (`$it.id == $actor.id` for an agent's own, "
                       "`$it.owner == $actor.id` for a type whose `owner` is owner), guard the read "
                       "(`$it.cash if $it.id == $actor.id else '?'`), or leave the private field out")
        self._private_via_defs(read, path)  # a def may read another entity's: whose shows only at run time

    def _picks_own(self, where: str | None, of: str) -> bool:
        """Whether a `where` passes only items that are the reader's own: a test that it is, alone or as a term of a
        top-level `and`."""
        try:
            tree = syntax_tree(str(where))
        except ExprError:
            return False
        terms = tree.values if isinstance(tree, ast.BoolOp) and isinstance(tree.op, ast.And) else [tree]
        return any(self._owns(term, of, f"{ROOT_PREFIX}actor", False) for term in terms)

    def _item_reads(self, source: str | None, top: str | None, reader: str = "actor", guarded: bool = False
                    ) -> list[str]:
        """The private properties (by name) ``source`` reads of an item (``$it``) that may not be the reader's own
        (the entity ``$<reader>`` names), in the order they are read: the one rule for every read of listed items, as
        the run reads them (each item as its reader does, see expr/hidden.py).

        ``$it`` is an entity of ``top`` outside any per-item function (the items a view, a choice or an inspect rule
        lists; None: it names nothing there), and each item of what a per-item function goes over inside one of its
        per-item arguments (an entity of the type it names, of any type when the items are worked out). A read is
        guarded — the item is the reader's own — where a test that it is (``$it.id`` or the type's ``owner``
        property against ``$<reader>``, e.g. ``$it.owner == $actor.id``) must have held for it to be read: after
        that test in an ``and``, after its negation in an ``or``, in the branch of an ``if`` it decides. ``guarded``:
        whether every ``$it`` at the top is already the reader's own (the items a `where` picked)."""
        if not source or not isinstance(source, str) or "$" not in source:
            return []
        try:
            tree = syntax_tree(source)
        except ExprError:
            return []  # reported by the expression check
        found: list[str] = []
        self._walk_items(tree, top, guarded, f"{ROOT_PREFIX}{reader}", found)
        return found

    def _walk_items(self, node: ast.AST, kind: str | None, guarded: bool, reader: str, found: list[str]) -> None:
        it = f"{ROOT_PREFIX}it"
        if isinstance(node, ast.BoolOp):
            held = guarded
            for value in node.values:
                self._walk_items(value, kind, held, reader, found)
                held = held or self._owns(value, kind, reader, isinstance(node.op, ast.Or))
            return
        if isinstance(node, ast.IfExp):
            self._walk_items(node.test, kind, guarded, reader, found)
            self._walk_items(node.body, kind, guarded or self._owns(node.test, kind, reader, False), reader, found)
            self._walk_items(node.orelse, kind, guarded or self._owns(node.test, kind, reader, True), reader, found)
            return
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == it:
            if kind is not None and not guarded and self._item_private(kind, node.attr) and node.attr not in found:
                found.append(node.attr)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith(FUNC_PREFIX):
            spec = FUNCTIONS.get(node.func.id[len(FUNC_PREFIX):])
            lazy = spec.lazy if spec is not None else frozenset()
            inner = self._items_of(node.args[0]) if node.args else _ITEMS_OF_ANY
            if node.args and isinstance(node.args[0], ast.Constant):
                lazy = frozenset()  # `$max(0, …)`: no items to go over, so every argument is read in place
            for index, arg in enumerate(node.args):
                if index in lazy:
                    self._walk_items(arg, inner, False, reader, found)
                else:
                    self._walk_items(arg, kind, guarded, reader, found)
            return
        for child in ast.iter_child_nodes(node):
            self._walk_items(child, kind, guarded, reader, found)

    def _items_of(self, node: ast.AST) -> str | None:
        """What the items of the collection ``node`` gives are: entities of the type it names, or that a function over
        such a type keeps (``$filter(story, …)``); None for record entries and events, whose fields are no entity's
        private property; entities of any type (:data:`_ITEMS_OF_ANY`) otherwise."""
        if isinstance(node, ast.Name) and node.id in self.c.types:
            return node.id
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith(FUNC_PREFIX):
            name = node.func.id[len(FUNC_PREFIX):]
            if name in ("records", "events"):
                return None
            if name in _KEEPS_ITEMS and node.args:
                return self._items_of(node.args[0])
        return _ITEMS_OF_ANY

    def _item_private(self, kind: str, field: str) -> bool:
        """Whether ``field`` of an item of ``kind`` (any type: :data:`_ITEMS_OF_ANY`) is hidden from the readers."""
        return self._private(self.c.types if kind == _ITEMS_OF_ANY else [kind], field)

    def _owns(self, test: ast.AST, kind: str | None, reader: str, negated: bool) -> bool:
        """Whether ``test`` (or, ``negated``, its failing) says the item is the reader's own: ``$it.id`` or the type's
        `owner` property compared with (``==``, ``in``; ``!=`` negated) what reads ``$<reader>``."""
        if kind is None or not isinstance(test, ast.Compare) or len(test.ops) != 1:
            return False
        op = test.ops[0]
        if not (isinstance(op, ast.NotEq) if negated else isinstance(op, (ast.Eq, ast.In))):
            return False
        owners = {"id"} | ({owner for name in self.c.types if (owner := self.c.owner_of(name))}
                           if kind == _ITEMS_OF_ANY else {self.c.owner_of(kind)} - {None})
        sides = [test.left, test.comparators[0]]
        names = [{sub.id for sub in ast.walk(side) if isinstance(sub, ast.Name)} for side in sides]
        for own, other in ((0, 1), (1, 0)):
            side = sides[own]
            if isinstance(side, ast.Attribute) and isinstance(side.value, ast.Name) \
                    and side.value.id == f"{ROOT_PREFIX}it" and side.attr in owners and reader in names[other]:
                return True
        return False

    def _private_inspect(self, kind: str, rule: str, path: str) -> None:
        """An inspect rule is read for each reader over every entity of ``kind`` it might inspect: a private property
        of the entity read where it may not be the reader's own is refused at run time, the first time an agent's
        tools are listed (what else it reads is checked as what each reader is offered, field_reads.py)."""
        with self._reading(self.agents):
            read = [f"$it.{name}" for name in self._item_reads(rule, kind, "viewer")]
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
        elif early := self._item_reads(where, of):
            self.error(path, f"reads private {', '.join(early)} of {of} before picking the actor's own: for a "
                             "choice the actor does not own, that read is an error at run time",
                       "test ownership first, as the first term of an `and` (`$it.<its owner property> == "
                       "$actor.id and ...`), or inside an `if` on it")
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
