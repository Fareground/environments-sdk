"""Who reads each field: the one table the privacy check and the run's gate both read.

Every field of the contract that can hold text — an expression, a template, a name — and every field of every core
effect operation is listed here once, with who reads what it holds, and so what a value hidden from some agent may do
there (``tests/kernel/test_kernel_field_readers.py`` fails on a field that is not listed, so none can be added
unclassified):

* :attr:`Reader.WORDS` — not an expression: a name, a setting, a description, a mechanism's config (read by the
  mechanism). Nothing in it is worked out, so nothing hidden can be read through it.
* :attr:`Reader.RULES` — game logic: it reads the world as it is, and a value hidden from the agent whose action is
  running counts as read by that agent (a refusal it decides spends the action). What the rules write is theirs to
  reveal: a public property, an entity's place, a link.
* :attr:`Reader.ONE` — what one agent is shown or offered (an outcome, a tool's bounds, its brief, a view): its own
  private values may show, no one else's. The run renders it for that agent.
* :attr:`Reader.SEVERAL` — what more than one agent is sent or learns from (an announcement, news, an entity's name,
  whether a stage was held, the order agents act in): no private value may show, not even the actor's own. The run
  works it out for everyone (``EVERYONE``).
* :attr:`Reader.ADDRESSED` — what is sent to chosen agents (a message's text and data, a record entry's fields, why
  an agent was woken): read as :attr:`ONE` by its one reader when there is exactly one, as :attr:`SEVERAL` when there
  are more; an entry sent to nobody in particular is read by whom its record's `visible` rule lets see it (all: as
  :attr:`SEVERAL`; a rule: the rules decide, as :attr:`RULES`).
* :attr:`Reader.WOKEN` — whom a stage wakes and how many passes it plays (its `who`, `until`, `passes`): the agents
  it wakes learn it, and so does everyone when it wakes everyone (no `who`) or announces its actions — then read as
  :attr:`SEVERAL`; a stage whose `who` picks agents and whose actions are not announced is the rules' (:attr:`RULES`).
* :attr:`Reader.EFFECTS` — an effect list: each of its effects is classified by its own fields (:data:`EFFECTS`).

Fields are named ``Model.field`` (the contract's models, by their class) and ``op.field`` (a core effect's). The
fields of a record entry a `post` writes are ``post.<field>``. Mechanisms are outside this table: each checks what its
own config shows.
"""
from __future__ import annotations

from enum import StrEnum

__all__ = ["Reader", "FIELDS", "EFFECTS", "reader_of"]


class Reader(StrEnum):
    """Who reads what a field holds (see the module docstring)."""

    WORDS = "words"
    RULES = "rules"
    ONE = "one"
    SEVERAL = "several"
    ADDRESSED = "addressed"
    WOKEN = "woken"
    EFFECTS = "effects"


W, R, O, S, A, K, E = (Reader.WORDS, Reader.RULES, Reader.ONE, Reader.SEVERAL, Reader.ADDRESSED, Reader.WOKEN,
                       Reader.EFFECTS)

#: Every contract field that can hold text or any value, by ``Model.field``.
FIELDS: dict[str, Reader] = {
    # the contract itself
    "Contract.fg_env": W, "Contract.name": W, "Contract.description": W, "Contract.imports": W,
    "Contract.mechanisms": W,
    # what each agent is told first: rendered for that agent
    "Brief.situation": O, "Brief.rules": O, "Brief.roles": O, "Brief.attach": O,
    # inputs are given, never worked out
    "InputSpec.type": W, "InputSpec.default": W, "InputSpec.values": W, "InputSpec.columns": W,
    "InputSpec.source": W, "InputSpec.description": W, "InputSpec.unit": W, "InputSpec.label": W,
    "InputSpec.caption": W, "InputSpec.alt": W, "InputSpec.tags": W, "InputSpec.describe": W,
    "InputSpec.display": W,
    # the run's length and its space: settings the build works out
    "Clock.rounds": R, "Clock.unit": W, "Clock.start": W,
    "GridSpace.rows": R, "GridSpace.cols": R, "GridSpace.neighborhood": W,
    "GraphSpace.nodes": R, "GraphSpace.edges": R, "PlaneSpace.width": R, "PlaneSpace.height": R,
    "Space.capacity": R, "LayerSpec.type": W, "LayerSpec.default": R, "LayerSpec.description": W,
    # properties: their values are the rules' to work out, their privacy is where they go
    "PropSpec.type": W, "PropSpec.default": R, "PropSpec.values": W, "PropSpec.private": W,
    "PropSpec.description": W, "PropSpec.unit": W,
    "TypeSpec.extends": W, "TypeSpec.description": W, "TypeSpec.owner": W, "TypeSpec.policy": W,
    # an inspect rule decides, for each reader, whether it may inspect an entity
    "TypeSpec.inspect": O,
    # a coded policy plays as its agent: it reads what that agent may know
    "PolicyRule.each": O, "PolicyRule.when": O, "PolicyRule.do": W, "PolicyRule.with_": O, "PolicyRule.chance": O,
    "ScoreSpec.value": R, "ScoreSpec.seat": R, "ScoreSpec.utility": W,
    # entities: a name and an id are text every agent reads; a brief is its own agent's
    "EntitySpec.type": W, "EntitySpec.name": S, "EntitySpec.id": S, "EntitySpec.props": R, "EntitySpec.at": R,
    "EntitySpec.brief": O, "EntitySpec.count": R, "EntitySpec.from_": W, "EntitySpec.where": R,
    "EntitySpec.weight": R,
    "LinkSpec.from_": R, "LinkSpec.to": R, "LinkSpec.value": R, "LinkSpec.among": R, "LinkSpec.graph": W,
    "LinkSpec.m": R, "LinkSpec.block": R, "LinkSpec.p_between": R, "LinkSpec.with_": R, "LinkSpec.hub": R,
    "LinkSpec.rows": W, "LinkSpec.degree": R, "LinkSpec.p": R, "LinkSpec.props": R, "LinkSpec.where": R,
    "RelationSpec.description": W,
    # records: how an entry reads, and who may see it, are worked out for each reader
    "RecordSpec.fields": W, "RecordSpec.show": O, "RecordSpec.visible": O, "RecordSpec.description": W,
    # actions: what the actor is shown and offered is its own; the announcement is everyone's
    "ActionSpec.by": W, "ActionSpec.description": W, "ActionSpec.do": E, "ActionSpec.outcome": O,
    "ActionSpec.announce": S, "ActionSpec.terminal": O, "ActionSpec.attach": O,
    "ParamSpec.type": W, "ParamSpec.of": W, "ParamSpec.where": O, "ParamSpec.values": O, "ParamSpec.min": O,
    "ParamSpec.max": O, "ParamSpec.min_items": O, "ParamSpec.max_items": O, "ParamSpec.default": O,
    "ParamSpec.invalid": O, "ParamSpec.kinds": W, "ParamSpec.description": W, "ParamSpec.overflow": W,
    # a requirement is game logic (a refusal it decides spends the action); why it failed is told to the actor
    "Condition.expr": R, "Condition.why": O,
    # stages: whether one was held and in what order agents act, every agent learns; whom it woke, those it woke
    "StageSpec.name": W, "StageSpec.when": S, "StageSpec.actions": W, "StageSpec.turns": W, "StageSpec.order": S,
    "StageSpec.who": K, "StageSpec.until": K, "StageSpec.passes": K, "StageSpec.quiet": W,
    "StageSpec.max_actions": R, "StageSpec.max_calls": R, "StageSpec.brief": O,
    # views: what each reader is shown
    "ViewSpec.for_": W, "ViewSpec.title": O, "ViewSpec.of": O, "ViewSpec.where": O, "ViewSpec.sort": O,
    "ViewSpec.show": O, "ViewSpec.empty": W, "ViewSpec.when": O, "ViewSpec.attach": O,
    # events: game logic whose headline is news to everyone
    "EventSpec.name": W, "EventSpec.on": W, "EventSpec.when": R, "EventSpec.do": E, "EventSpec.say": S,
    "OutputSpec.expr": R, "OutputSpec.type": W, "OutputSpec.description": W, "OutputSpec.unit": W,
    "OutputSpec.format": W, "OutputSpec.series": R,
    # the end is the rules' reveal; its words are news to everyone
    "EndSpec.when": R, "EndSpec.name": W, "EndSpec.winner": R, "EndSpec.say": S, "EndSpec.check": W,
    "ArmSpec.description": W, "ArmSpec.inputs": W, "ArmSpec.patch": W,
    # an invariant is the rules'; why it broke is sent to everyone (whose action broke it is not known)
    "InvariantSpec.expr": R, "InvariantSpec.why": S, "InvariantSpec.check": W,
    # a def is read in its caller's place
    "DefSpec.args": W, "DefSpec.expr": R, "DefSpec.do": E, "DefSpec.description": W,
}

#: Every core effect operation's fields, by operation (``effects/shapes.py`` lists their shapes); ``post.*`` stands for
#: the fields of the record entry a `post` writes.
EFFECTS: dict[str, dict[str, Reader]] = {
    "if": {"if": R, "then": E, "else": E},
    "each": {"each": R, "where": R, "do": E, "as": W, "sync": W},
    "create": {"create": W, "count": R, "id": S, "name": S, "props": R, "at": R, "as": W},
    "remove": {"remove": R},
    "transfer": {"transfer": W, "from": R, "to": R, "amount": R, "into": W},
    "link": {"link": W, "from": R, "to": R, "value": R, "props": R},
    "unlink": {"unlink": W, "from": R, "to": R},
    "move": {"move": R, "to": R},
    "post": {"post": W, "to": R, "author": R, "delay": R, "drop": R, "*": A},
    "emit": {"emit": W, "say": A, "to": R, "data": A, "delay": R, "drop": R},
    "fail": {"fail": O},
    "end": {"end": W, "winner": R, "say": S},
    "after": {"after": R, "do": E},
    "wake": {"wake": R, "why": A, "now": R, "actions": W},
    "repeat": {"repeat": R, "while": R, "do": E},
    "call": {"call": W, "with": R},
    "chance": {"chance": R, "outcomes": R, "weight": R, "as": W, "do": E},
}


def reader_of(field: str) -> Reader:
    """Who reads ``field`` (``Model.field``, or ``op.field`` of a core effect; see the module docstring)."""
    if field in FIELDS:
        return FIELDS[field]
    op, _, key = field.partition(".")
    fields = EFFECTS.get(op)
    if fields is None:
        raise KeyError(f"no reader is declared for {field}")
    return fields.get(key) or fields["*"]
