"""Checking every field for hidden values by who reads it: the table of ``contract/readers.py``, which the run's gate
reads too (a part of the contract checker).

Each field of the contract, and each field of each core effect, is checked by its reader class, one rule per class:

* what one agent is shown or offered (`ONE`) may read that agent's own private values and nothing else hidden from
  it — another agent's, the world's, one fetched by id, an output worked out from them;
* what several agents are sent or learn from (`SEVERAL`) may read no private value at all, not even the actor's own
  (the records and events it reads are those every agent sees; a stage, which decides by them, may not read the
  others: it would decide by fewer than it means to);
* what is sent to chosen agents (`ADDRESSED`) is read as `ONE` by its one reader when the field says there is one (a
  message `to` ``$actor``, ``$it``, …), and as `SEVERAL` otherwise; an entry sent to nobody in particular, by its
  record's readers;
* whom a stage wakes and how often it passes (`WOKEN`) is `SEVERAL` when everyone learns it;
* the rules' own fields (`RULES`) and plain words are the rules' to read.

Where items are listed for a reader — a view of a type, an entity choice, an inspect rule — which item is the reader's
own shows only item by item: those reads are checked where the items are picked (:mod:`.privacy`).
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .. import contract as C
from ..actions.book import announces
from ..contract.readers import FIELDS, Reader, reader_of
from ..expr import is_expr
from ..information.perception import SPECTATOR
from .privacy import PrivacyChecks, _expressions

if TYPE_CHECKING:
    from .roots import Types

__all__ = ["FieldReads"]

#: What every agent learns from a field whose words are not sent to anyone: the rest say "is sent to".
_LEARNS = {"StageSpec.when": "whether the stage was held (it names the stage it plays)",
           "StageSpec.order": "the order the agents act in",
           "StageSpec.who": "whom the stage wakes (its actions are announced)",
           "StageSpec.until": "how many passes the stage played (each wakes them again)",
           "StageSpec.passes": "how many passes the stage played (each wakes them again)",
           "EntitySpec.name": "each entity's name", "EntitySpec.id": "each entity's id",
           "create.name": "each entity's name", "create.id": "each entity's id"}
#: A bare reader of a message: ``$it``, ``$actor.id``, ``[$params.who]``.
_ONE_READER = re.compile(r"\s*\[?\s*\$([A-Za-z_][A-Za-z0-9_]*)(?:\.id)?\s*\]?\s*")
#: A message sent to a named entity: ``$entity(ann)``, ``$entity('ann')``.
_NAMED_READER = r"\$entity\(\s*(['\"]?){id}\1\s*\)"
#: The root that stands for a named entity a message is sent to, while what it may read is checked.
_ADDRESSEE = "addressee"
#: The fields whose `$it` items are picked for a reader item by item (checked in :mod:`.privacy`).
_LISTED = frozenset({"ViewSpec.where", "ViewSpec.sort", "ViewSpec.show", "ViewSpec.attach", "ParamSpec.where",
                     "TypeSpec.inspect"})


class FieldReads(PrivacyChecks):
    """Each field checked by who reads it (see the module docstring)."""

    # -- the contract's own fields ----------------------------------------------------------------------------------

    def _field_reads(self) -> None:
        """Every contract field, by its reader class, with what it binds: its reader, and the roots it reads."""
        c, agents = self.c, set(self.agents)
        every = {"actor": agents}
        self._model(c, "", {})
        self._model(c.brief, "brief", every, reader="actor", implicit="actor", skip={"roles"})
        for kind, text in c.brief.roles.items():
            self._read("Brief.roles", text, f"brief.roles.{kind}", {"actor": self._kinds(kind)}, reader="actor",
                       implicit="actor")
        for name, given in c.inputs.items():
            self._model(given, f"inputs.{name}", {})
        self._model(c.clock, "clock", {})
        if c.space is not None:
            self._spaces(c.space, "space")
        for name, prop in c.world.items():
            self._model(prop, f"world.{name}", {})
        for kind, declared in c.types.items():
            self._model(declared, f"types.{kind}", {"it": {kind}}, reader="viewer")
            for name, prop in declared.props.items():
                self._model(prop, f"types.{kind}.props.{name}", {"it": {kind}})
            for name, policy in declared.policies.items():
                for index, rule in enumerate(policy.rules):
                    self._model(rule, f"types.{kind}.policies.{name}.rules[{index}]", {"actor": self._kinds(kind)},
                                reader="actor")
            if declared.score is not None:
                self._model(declared.score, f"types.{kind}.score", {})
        for key, entity in c.entities.items():
            self._model(entity, f"entities.{key}", {"actor": self._kinds(entity.type)}, reader="actor",
                        implicit="actor")
        for name, relation in c.relations.items():
            self._model(relation, f"relations.{name}", {})
            for index, link in enumerate(relation.links):
                self._model(link, f"relations.{name}.links[{index}]", {})
        for name, record in c.records.items():
            self._model(record, f"records.{name}", {}, implicit="it")  # read by each reader of an entry
        for name, action in c.actions.items():
            self._action(name, action)
        for index, stage in enumerate(c.stages):
            self._stage(stage, f"stages[{index}]")
        for name, view in c.views.items():
            self._view(view, f"views.{name}")
        for index, event in enumerate(c.events):
            self._model(event, f"events[{index}]", {})
        for name, output in c.outputs.items():
            self._model(output, f"outputs.{name}", {})
        for index, end in enumerate(c.end):
            self._model(end, f"end[{index}]", {})
        for name, arm in c.arms.items():
            self._model(arm, f"arms.{name}", {})
        for index, invariant in enumerate(c.invariants):
            self._model(invariant, f"invariants[{index}]", {})
        for name, def_ in c.defs.items():
            self._model(def_, f"defs.{name}", {})

    def _spaces(self, space: C.Space, path: str) -> None:
        self._model(space, path, {})
        for key in ("grid", "graph", "plane"):
            part = getattr(space, key, None)
            if isinstance(part, BaseModel):
                self._model(part, f"{path}.{key}", {})
        for name, layer in (space.layers or {}).items():
            self._model(layer, f"{path}.layers.{name}", {})

    def _action(self, name: str, action: C.ActionSpec) -> None:
        path = f"actions.{name}"
        by = {kind for kind in ([action.by] if isinstance(action.by, str) else action.by) if kind in self.c.types}
        actor = {"actor": set().union(*(self._kinds(kind) for kind in by)) if by else set()}
        self._model(action, path, actor, reader="actor", params=action.params, implicit="actor")
        for pname, param in action.params.items():
            self._param(param, f"{path}.params.{pname}", actor, action.params)
        for index, condition in enumerate(action.when):
            self._model(condition, f"{path}.when[{index}]", actor, reader="actor", params=action.params,
                        implicit="actor")

    def _param(self, param: C.ParamSpec, path: str, actor: Types, params: Mapping[str, C.ParamSpec]) -> None:
        self._model(param, path, actor, reader="actor", params=params, implicit="actor")
        if param.items is not None:
            self._param(param.items, f"{path}.items", actor, params)

    def _stage(self, stage: C.StageSpec, path: str) -> None:
        actors = {"actor": set(self.agents)}
        self._stage_now = stage
        self._model(stage, path, {**actors, "it": set(self.agents)}, reader="actor", implicit="actor")
        for index, condition in enumerate(stage.valid):
            self._model(condition, f"{path}.valid[{index}]", actors, reader="actor", implicit="actor")

    def _view(self, view: C.ViewSpec, path: str) -> None:
        targets = [view.for_] if isinstance(view.for_, str) else list(view.for_)
        if targets == [SPECTATOR]:
            return  # watched from outside the game: no agent reads it
        readers = set(self.agents) if "all" in targets else \
            set().union(*(self._kinds(kind) for kind in targets if kind in self.c.types))
        self._model(view, path, {"actor": readers}, reader="actor", implicit="actor")

    def _model(self, model: BaseModel, path: str, types: Types, *, reader: str | None = None,
               params: Mapping[str, C.ParamSpec] | None = None, implicit: str | None = None,
               skip: frozenset[str] | set[str] = frozenset()) -> None:
        """Check each field of ``model`` the table lists, by its reader class."""
        kind = type(model).__name__
        for name, info in type(model).model_fields.items():
            field = f"{kind}.{name}"
            if field not in FIELDS or name in skip:
                continue
            value = getattr(model, name)
            if value is None or value == "" or value is False:
                continue
            at = f"{path}.{info.alias or name}" if path else (info.alias or name)
            here = implicit
            if field == "ViewSpec.show" and getattr(model, "of", None) is not None:
                here = "it"
            self._read(field, value, at, types, reader=reader, params=params, implicit=here,
                       listed=field in _LISTED and getattr(model, "of", kind == "TypeSpec") in self.c.types
                       if kind != "TypeSpec" else True)

    def _kinds(self, kind: str) -> set[str]:
        """``kind`` and every type that extends it: an agent of any of them is one."""
        return {name for name in self.c.types if kind in self.c.lineage(name)}

    # -- one field ---------------------------------------------------------------------------------------------------

    def _read(self, field: str, value: Any, path: str, types: Types, *, reader: str | None = None,
              params: Mapping[str, C.ParamSpec] | None = None, implicit: str | None = None,
              one: str | None = None, named: bool = False, listed: bool = False) -> None:
        """Check ``value`` (the text, or the texts inside it) of ``field`` at ``path`` by who reads it. ``reader`` is
        the root that names the one agent a `ONE` field is shown to; ``one``, for an `ADDRESSED` field, the root that
        names its one reader (None: it reaches several), and ``named`` whether the field names whom it is sent to.
        ``listed``: the field reads items listed for its reader, whose `$it` reads are checked where they are picked
        (:mod:`.privacy`)."""
        kind = reader_of(field)
        if kind is Reader.WOKEN:
            stage = getattr(self, "_stage_now", None)
            kind = Reader.SEVERAL if stage is None or stage.who is None or announces(self.c, stage) else Reader.RULES
        addressed = kind is Reader.ADDRESSED
        if addressed:
            kind, reader = (Reader.ONE, one) if one is not None else (Reader.SEVERAL, None)
        if kind is Reader.ONE:
            named_entity = reader[len("entity:"):] if reader is not None and reader.startswith("entity:") else None
            if named_entity is not None:  # read as its one reader reads it: the entity it is sent to is its own
                spec = self.c.named_entities().get(named_entity)
                types = {**types, _ADDRESSEE: self._kinds(spec.type) if spec is not None else set()}
                reader = _ADDRESSEE
            for at, text in _texts(value, path):
                if named_entity is not None:
                    text = re.sub(_NAMED_READER.format(id=re.escape(named_entity)), f"${_ADDRESSEE}", text)
                self._shown_one(text, at, types, reader, params or {}, implicit, listed, addressed)
        elif kind is Reader.SEVERAL:
            for at, text in _texts(value, path):
                self._shown_several(field, text, at, types, params or {}, implicit, addressed and named)

    def _shown_one(self, text: str, path: str, types: Types, reader: str | None,
                   params: Mapping[str, C.ParamSpec], implicit: str | None, listed: bool,
                   addressed: bool = False) -> None:
        """What one agent (the root ``reader``) is shown or offered: its own private values may show, nothing else
        hidden from it."""
        expressions = _expressions(text, implicit)
        if not expressions:
            return
        own = types.get(reader, set()) if reader is not None else set()
        others = {root: kinds for root, kinds in types.items() if root != reader and not (listed and root == "it")}
        # a choice whose `where` picks only the reader's own entities (`$it.owner == $actor.id`) is its own to read
        chosen = {name: spec for name, spec in params.items() if reader != "actor" or not self._own_choice(spec)}
        with self._reading(own):
            read = self._hidden_reads(expressions, others, chosen, items=False) | self._fetched_reads(expressions)
            if reader is not None and not listed:  # each item of a per-item function, where it may not be its own
                read |= {f"{name} of an item that may not be its reader's own"
                         for expr in expressions for name in self._item_reads(expr.source, None, reader)}
        if read:
            what = "what is sent to one agent may show only its one reader's private properties" if addressed else \
                "what one agent is shown or offered here is refused a value hidden from it"
            self.error(path, f"reads private {', '.join(sorted(read))}: {what}, and the engine refuses it at run time",
                       "work out what the agent may learn in game logic (`\"$seen = $params.target.role\"` in an "
                       "action's do) and show `{$seen}`, or read only its own (`$actor.<its property>`)")

    def _own_choice(self, param: C.ParamSpec) -> bool:
        """Whether an entity parameter's choices are only its actor's own entities: its `where` picks them."""
        return param.type == "entity" and param.of in self.c.types and self._ownable(param.of) \
            and param.where is not None and self._picks_own(param.where, param.of)

    def _shown_several(self, field: str, text: str, path: str, types: Types, params: Mapping[str, C.ParamSpec],
                       implicit: str | None, addressed: bool = False) -> None:
        """What several agents are sent or learn from: no private value, not even the actor's own."""
        expressions = _expressions(text, implicit)
        if not expressions:
            return
        read = self._hidden_reads(expressions, types, params) | self._fetched_reads(expressions)
        read |= {f"${name} (worked out from private properties)" for expr in expressions
                 for name in (expr.roots | expr.functions) & self._private_defs}
        if field.startswith("StageSpec."):  # a stage's decision reads what every agent sees, never what it means to
            read |= self._log_reads(expressions)
        if not read:
            return
        learns = _LEARNS.get(field)
        what = f"every agent learns {learns}" if learns else \
            "it is sent to whom its `to` names, which is not one agent the check can name (`$actor`, `$it`, " \
            "`$params.<who>`), so it is read as sent to several" if addressed else "this is sent to more than one agent"
        self.error(path, f"reads private {', '.join(sorted(read))}, and {what}: the engine refuses it at run time",
                   "work out what they may learn in game logic and show that (in an action, `\"$shown = "
                   "$actor.cash\"` in `do`, then `{$shown}`; in an event, a property that is not private), or read "
                   "what is not private")

    # -- effects -----------------------------------------------------------------------------------------------------

    def effect_reads(self, op: str, effect: Mapping[str, Any], path: str, roots: set[str], types: Types,
                     params: Mapping[str, C.ParamSpec] | None) -> None:
        """Check each field of the core effect ``effect`` (operation ``op``) by who reads it (see the table)."""
        for key, value in effect.items():
            if key == op and reader_of(f"{op}.{op}") is Reader.WORDS:
                continue
            field = f"{op}.{key}"
            try:
                reader_of(field)
            except KeyError:
                continue  # reported as a field the operation does not take
            one = self._one_reader(op, key, effect, roots) if reader_of(field) is Reader.ADDRESSED else None
            if reader_of(field) is Reader.ADDRESSED and one is None and self._rules_decide(op, effect):
                continue  # an entry its record's `visible` rule shows: the rules decide who reads it
            self._read(field, value, f"{path}.{key}", types, reader="actor" if "actor" in roots else None,
                       params=params, one=one, named=op == "wake" or "to" in effect)

    def _one_reader(self, op: str, key: str, effect: Mapping[str, Any], roots: set[str]) -> str | None:
        """The root naming the one agent what ``effect`` sends is read by, when the effect names exactly one: its `to`
        (a `wake`'s own target), and for an entry its author too (the actor, unless it names another)."""
        target = effect.get("wake") if op == "wake" else effect.get("to")
        if not isinstance(target, str):
            return None
        match = _ONE_READER.fullmatch(target)
        named = next((entity for entity in self.c.named_entities()
                      if re.fullmatch(r"\s*\[?\s*" + _NAMED_READER.format(id=re.escape(entity)) + r"\s*\]?\s*",
                                      target)), None)
        if match is None and named is None:
            return None
        root = match.group(1) if match is not None else f"entity:{named}"
        if op == "post":
            author = effect.get("author")
            written = _ONE_READER.fullmatch(author) if isinstance(author, str) else None
            named = written.group(1) if written is not None else ("actor" if "actor" in roots and author is None
                                                                  else None)
            if named is not None and named != root:
                return None  # its author reads it too
        return root

    def _rules_decide(self, op: str, effect: Mapping[str, Any]) -> bool:
        """An entry sent to nobody in particular, of a record whose `visible` rule decides who reads it."""
        if op != "post" or "to" in effect:
            return False
        spec = self.c.records.get(effect.get("post"))  # type: ignore[arg-type]
        return spec is not None and spec.visible != "all"


def _texts(value: Any, path: str) -> Iterator[tuple[str, str]]:
    """The texts inside a field's value, each with its path: the value itself, an object's values, a list's items."""
    if isinstance(value, str):
        if is_expr(value) or "{" in value:
            yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _texts(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _texts(item, f"{path}[{index}]")
