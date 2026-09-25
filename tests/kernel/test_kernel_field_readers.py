"""Every field is classified by who reads it, and the check and the run agree about every one (audit 14).

``contract/readers.py`` is the one table of who reads each field; the privacy check and the run's gate both read it.
Two properties hold it to the contract:

* the table lists exactly the fields that can hold text or any value — every leaf of the contract's models and every
  field of every core effect — so a field cannot be added without saying who reads it;
* for every field, a contract that reads a value hidden from every agent there (the world's private ``hid``) is
  either refused by ``check(rounds=0)`` at that field, or plays to its end without the run refusing a private read,
  and changing the hidden value changes nothing another agent is shown while what it may see of the world is the same
  (``_noninterference.leaks``): the static check and the run's gate never disagree, and nothing leaks past both.
"""
from __future__ import annotations

import copy
import typing
from collections.abc import Callable
from typing import Any

import pytest
from _noninterference import leaks, play
from pydantic import BaseModel

import fg_env
from fg_env.contract import Contract
from fg_env.contract.readers import EFFECTS, FIELDS
from fg_env.effects.shapes import EFFECT_FIELDS

#: The hidden value in the first world, and in the second.
HIDDEN = (7, 8)
#: A read of it: as an expression of each kind of value, and in a template.
NUM, INT, BOOL, TEXT = "$world.hid", "$world.hid - 6", "$world.hid > 7", "{$world.hid}"
ENTITY = "$entity('a') if $world.hid > 7 else $entity('b')"


# -- the table lists every field ------------------------------------------------------------------------------------


def _leaves(annotation: Any) -> list[Any]:
    """The kinds of value a field holds (a map's values, a list's items), models and all."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _leaves(typing.get_args(annotation)[0])
    if origin is dict:
        return _leaves(typing.get_args(annotation)[1])
    if origin is typing.Literal:
        return [type(value) for value in typing.get_args(annotation)]
    args = [arg for arg in typing.get_args(annotation) if arg is not Ellipsis]
    return [leaf for arg in args for leaf in _leaves(arg)] if args else [annotation]


def _text_fields() -> set[str]:
    """Every field of the contract's models that can hold text or any value, as ``Model.field``."""
    found: set[str] = set()
    seen: set[type] = set()
    pending: list[type] = [Contract]
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        for name, info in model.model_fields.items():
            leaves = _leaves(info.annotation)
            if any(leaf is str or leaf is Any for leaf in leaves):
                found.add(f"{model.__name__}.{name}")
            pending += [leaf for leaf in leaves if isinstance(leaf, type) and issubclass(leaf, BaseModel)]
    return found


def test_the_table_lists_every_field_that_can_hold_text_and_no_other():
    assert set(FIELDS) == _text_fields()


def test_the_table_lists_every_field_of_every_core_effect():
    assert set(EFFECTS) == set(EFFECT_FIELDS)
    for op, fields in EFFECT_FIELDS.items():
        listed = set(EFFECTS[op]) - {"*"}
        assert listed == set(fields), op
    assert set(EFFECTS["post"]) - set(EFFECT_FIELDS["post"]) == {"*"}  # the fields of the entry it writes


# -- a hidden read in every field ------------------------------------------------------------------------------------


def base() -> dict[str, Any]:
    """A contract holding an instance of every model, whose hidden value (``$world.hid``) nothing reads."""
    return {
        "name": "Fields",
        "world": {"hid": {"type": "int", "default": HIDDEN[0], "private": True}, "pub": 0},
        "types": {
            "p": {"agent": True, "inspect": True,
                  "props": {"k": 0, "mine": {"type": "int", "default": 0, "private": True}},
                  "policies": {"x": {"rules": [{"do": "go", "with": {"n": 1}}, {"do": "pass"}]}}},
            "token": {"inspect": True, "props": {"v": 0, "held": {"type": "int", "default": 0, "private": True}}},
        },
        "entities": {"a": {"type": "p"}, "b": {"type": "p"}, "t1": {"type": "token"}},
        "relations": {"knows": {"links": [{"from": "a", "to": "b"}]}},
        "records": {"log": {"fields": {"text": "text"}, "show": "{text}"}},
        "actions": {"go": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": 3, "default": 1}},
                           "do": ["$actor.k += 1"]}},
        "stages": [{"name": "s"}],
        "views": {"v": {"show": "k {$actor.k}"}, "tokens": {"of": "token", "show": "{name} {v}"}},
        "outputs": {"x": "1"},
        "clock": {"rounds": 2},
    }


def _set(path: list[Any], value: Any) -> Callable[[dict[str, Any]], str]:
    """A placement: ``value`` at ``path`` in the contract (made where missing); the dotted path it names."""
    def place(c: dict[str, Any]) -> str:
        node: Any = c
        for key, nxt in zip(path, path[1:]):
            if isinstance(node, list):
                node = node[key]
            else:
                node = node.setdefault(key, [] if isinstance(nxt, int) else {})
        node[path[-1]] = copy.deepcopy(value)
        return ".".join(f"[{key}]" if isinstance(key, int) else str(key) for key in path).replace(".[", "[")
    return place


def _effect(effect: dict[str, Any], field: str, where: str = "action") -> Callable[[dict[str, Any]], str]:
    """A placement: ``effect`` in the action's `do` (or a round's start event), its field ``field``."""
    def place(c: dict[str, Any]) -> str:
        if where == "action":
            c["actions"]["go"]["do"] = [copy.deepcopy(effect)]
            return f"actions.go.do[0].{field}"
        c["events"] = [{"on": "round.start", "do": [copy.deepcopy(effect)]}]
        return f"events[0].do[0].{field}"
    return place


def _both(*places: Callable[[dict[str, Any]], str]) -> Callable[[dict[str, Any]], str]:
    def place(c: dict[str, Any]) -> str:
        paths = [each(c) for each in places]
        return paths[-1]
    return place


#: Where to read the hidden value in each field, spelled as the field takes it.
PLACES: dict[str, Callable[[dict[str, Any]], str]] = {
    "Contract.fg_env": _set(["fg_env"], TEXT), "Contract.name": _set(["name"], TEXT),
    "Contract.description": _set(["description"], TEXT), "Contract.imports": _set(["imports"], []),  # file paths
    "Contract.mechanisms": _set(["mechanisms"], {}),
    "Brief.situation": _set(["brief", "situation"], TEXT), "Brief.rules": _set(["brief", "rules"], TEXT),
    "Brief.roles": _set(["brief", "roles", "p"], TEXT), "Brief.attach": _set(["brief", "attach"], TEXT),
    "InputSpec.type": _set(["inputs", "i", "type"], TEXT), "InputSpec.default": _set(["inputs", "i", "default"], NUM),
    "InputSpec.values": _set(["inputs", "i"], {"type": "enum", "values": [NUM], "default": NUM}),
    "InputSpec.columns": _set(["inputs", "i"], {"type": "table", "columns": {"c": TEXT}, "default": []}),
    "InputSpec.source": _set(["inputs", "i"], {"type": "table", "source": TEXT}),
    "InputSpec.description": _set(["inputs", "i"], {"default": 1, "description": TEXT}),
    "InputSpec.unit": _set(["inputs", "i"], {"default": 1, "unit": TEXT}),
    "InputSpec.label": _set(["inputs", "i"], {"default": 1, "label": TEXT}),
    "InputSpec.caption": _set(["inputs", "i"], {"default": 1, "caption": TEXT}),
    "InputSpec.alt": _set(["inputs", "i"], {"default": 1, "alt": TEXT}),
    "InputSpec.tags": _set(["inputs", "i"], {"default": 1, "tags": [TEXT]}),
    "InputSpec.describe": _set(["inputs", "i"], {"default": 1, "describe": TEXT}),
    "InputSpec.display": _set(["inputs", "i"], {"default": 1, "display": "text"}),
    "Clock.rounds": _set(["clock", "rounds"], INT), "Clock.unit": _set(["clock", "unit"], TEXT),
    "Clock.start": _set(["clock", "start"], TEXT),
    "GridSpace.rows": _set(["space"], {"grid": {"rows": INT, "cols": 2}}),
    "GridSpace.cols": _set(["space"], {"grid": {"rows": 2, "cols": INT}}),
    "GridSpace.neighborhood": _set(["space"], {"grid": {"rows": 2, "cols": 2, "neighborhood": TEXT}}),
    "GraphSpace.nodes": _set(["space"], {"graph": {"nodes": NUM, "edges": []}}),
    "GraphSpace.edges": _set(["space"], {"graph": {"nodes": ["n1", "n2"], "edges": NUM}}),
    "PlaneSpace.width": _set(["space"], {"plane": {"width": NUM, "height": 5}}),
    "PlaneSpace.height": _set(["space"], {"plane": {"width": 5, "height": NUM}}),
    "Space.capacity": _set(["space"], {"grid": {"rows": 2, "cols": 2}, "capacity": INT}),
    "LayerSpec.type": _set(["space"], {"grid": {"rows": 2, "cols": 2}, "layers": {"soil": {"type": TEXT}}}),
    "LayerSpec.default": _set(["space"], {"grid": {"rows": 2, "cols": 2}, "layers": {"soil": {"default": NUM}}}),
    "LayerSpec.description": _set(["space"], {"grid": {"rows": 2, "cols": 2},
                                              "layers": {"soil": {"default": 1, "description": TEXT}}}),
    "PropSpec.type": _set(["types", "p", "props", "extra"], {"type": TEXT}),
    "PropSpec.default": _set(["types", "p", "props", "mine"], {"type": "int", "default": NUM, "private": True}),
    "PropSpec.values": _set(["types", "p", "props", "extra"], {"type": "enum", "values": [TEXT, "x"], "default": "x"}),
    "PropSpec.private": _set(["types", "p", "props", "extra"], {"default": 0, "private": [TEXT]}),
    "PropSpec.description": _set(["types", "p", "props", "extra"], {"default": 0, "description": TEXT}),
    "PropSpec.unit": _set(["types", "p", "props", "extra"], {"default": 0, "unit": TEXT}),
    "TypeSpec.extends": _set(["types", "q"], {"extends": TEXT}),
    "TypeSpec.description": _set(["types", "p", "description"], TEXT),
    "TypeSpec.owner": _set(["types", "token", "owner"], TEXT),
    "TypeSpec.policy": _set(["types", "p", "policy"], TEXT),
    "TypeSpec.inspect": _set(["types", "token", "inspect"], BOOL),
    "PolicyRule.each": _set(["types", "p", "policies", "x", "rules", 0, "each"], "[] if $world.hid > 7 else [1]"),
    "PolicyRule.when": _set(["types", "p", "policies", "x", "rules", 0, "when"], BOOL),
    "PolicyRule.do": _set(["types", "p", "policies", "x", "rules", 0, "do"], TEXT),
    "PolicyRule.with_": _set(["types", "p", "policies", "x", "rules", 0, "with", "n"], INT),
    "PolicyRule.chance": _set(["types", "p", "policies", "x", "rules", 0, "chance"], "($world.hid - 7) / 2"),
    "ScoreSpec.value": _set(["types", "p", "score"], {"value": NUM}),
    "ScoreSpec.seat": _set(["types", "p", "score"], {"value": "$actor.k", "seat": TEXT}),
    "ScoreSpec.utility": _set(["types", "p", "score"], {"value": "$actor.k", "utility": TEXT}),
    "EntitySpec.type": _set(["entities", "t2"], {"type": TEXT}),
    "EntitySpec.name": _set(["entities", "t1", "name"], "T" + TEXT),
    "EntitySpec.id": _set(["entities", "tok"], {"type": "token", "count": 1, "id": "tok" + TEXT}),
    "EntitySpec.props": _set(["entities", "a", "props", "mine"], NUM),
    "EntitySpec.at": _both(_set(["space"], {"grid": {"rows": 3, "cols": 3}}),
                           _set(["entities", "t1", "at"], "[$world.hid - 6, 0]")),
    "EntitySpec.brief": _set(["entities", "a", "brief"], TEXT),
    "EntitySpec.count": _set(["entities", "tok"], {"type": "token", "count": INT}),
    "EntitySpec.from_": _set(["entities", "tok"], {"type": "token", "from": TEXT}),
    "EntitySpec.where": _both(_set(["inputs", "rows"], {"type": "table", "columns": {"n": "int"},
                                                       "default": [{"n": 1}, {"n": 2}]}),
                              _set(["entities", "tok"], {"type": "token", "from": "$inputs.rows",
                                                         "where": "$row.n < $world.hid - 6"})),
    "EntitySpec.weight": _both(_set(["inputs", "rows"], {"type": "table", "columns": {"n": "int"},
                                                        "default": [{"n": 1}, {"n": 2}]}),
                               _set(["entities", "tok"], {"type": "token", "from": "$inputs.rows", "count": 1,
                                                          "weight": "$world.hid - 6"})),
    "RelationSpec.description": _set(["relations", "knows", "description"], TEXT),
    **{f"LinkSpec.{name}": _set(["relations", "knows", "links", 0], spec) for name, spec in {
        "from_": {"from": ENTITY, "to": "b"}, "to": {"from": "a", "to": ENTITY},
        "value": {"from": "a", "to": "b", "value": NUM}, "among": {"among": "p if $world.hid > 7 else token"},
        "graph": {"among": "p", "graph": TEXT}, "m": {"among": "p", "graph": "preferential", "m": INT},
        "block": {"among": "p", "graph": "blocks", "block": "$world.hid"},
        "p_between": {"among": "p", "graph": "blocks", "block": "$it.k", "p_between": "($world.hid - 7) / 2"},
        "with_": {"from": "a", "with": ENTITY}, "hub": {"among": "p", "graph": "star", "hub": ENTITY},
        "rows": {"rows": TEXT}, "degree": {"among": "p", "graph": "ring", "degree": INT},
        "p": {"among": "p", "graph": "random", "p": "($world.hid - 7) / 2"},
        "props": {"from": "a", "to": "b", "props": {"w": NUM}},
        "where": {"among": "p", "where": BOOL}}.items()},
    "RecordSpec.fields": _set(["records", "log", "fields", "text"], TEXT),
    "RecordSpec.show": _set(["records", "log", "show"], TEXT),
    "RecordSpec.visible": _set(["records", "log", "visible"], BOOL),
    "RecordSpec.description": _set(["records", "log", "description"], TEXT),
    "ActionSpec.by": _set(["actions", "go", "by"], TEXT),
    "ActionSpec.description": _set(["actions", "go", "description"], TEXT),
    "ActionSpec.do": _set(["actions", "go", "do"], ["$actor.k += $world.hid - 6"]),
    "ActionSpec.outcome": _set(["actions", "go", "outcome"], TEXT),
    "ActionSpec.announce": _set(["actions", "go", "announce"], TEXT),
    "ActionSpec.terminal": _set(["actions", "go", "terminal"], BOOL),
    "ActionSpec.attach": _both(_set(["inputs", "pic"], {"type": "file", "source": "missing.png"}),
                               _set(["actions", "go", "attach"], "'pic' if $world.hid > 7 else null")),
    "ParamSpec.type": _set(["actions", "go", "params", "n", "type"], TEXT),
    "ParamSpec.of": _set(["actions", "go", "params", "t"], {"type": "entity", "of": TEXT}),
    "ParamSpec.where": _set(["actions", "go", "params", "t"], {"type": "entity", "of": "token", "where": BOOL}),
    "ParamSpec.values": _set(["actions", "go", "params", "e"], {"type": "enum", "values": "[1, $world.hid]",
                                                                "required": False}),
    "ParamSpec.min": _set(["actions", "go", "params", "n", "min"], "$world.hid - 7"),
    "ParamSpec.max": _set(["actions", "go", "params", "n", "max"], INT),
    "ParamSpec.min_items": _set(["actions", "go", "params", "l"], {"type": "list", "items": {"type": "int"},
                                                                   "min_items": "$world.hid - 7", "required": False}),
    "ParamSpec.max_items": _set(["actions", "go", "params", "l"], {"type": "list", "items": {"type": "int"},
                                                                   "max_items": INT, "required": False}),
    "ParamSpec.default": _set(["actions", "go", "params", "n", "default"], "$world.hid - 7"),
    "ParamSpec.invalid": _set(["actions", "go", "params", "n", "invalid"], TEXT),
    "ParamSpec.kinds": _set(["actions", "go", "params", "f"], {"type": "file", "kinds": [TEXT], "required": False}),
    "ParamSpec.description": _set(["actions", "go", "params", "n", "description"], TEXT),
    "ParamSpec.overflow": _set(["actions", "go", "params", "w"], {"type": "text", "overflow": "truncate",
                                                                  "required": False}),
    "Condition.expr": _set(["actions", "go", "when"], [BOOL]),
    "Condition.why": _set(["actions", "go", "when"], [{"expr": "$round > 5", "why": TEXT}]),
    "StageSpec.name": _set(["stages", 0, "name"], TEXT),
    "StageSpec.when": _set(["stages", 0, "when"], BOOL),
    "StageSpec.actions": _set(["stages", 0, "actions"], [TEXT]),
    "StageSpec.turns": _set(["stages", 0, "turns"], TEXT),
    "StageSpec.order": _set(["stages", 0, "order"], "$world.hid * $it.k"),
    "StageSpec.who": _set(["stages", 0, "who"], "$world.hid > 7 or $it.id == 'b'"),
    "StageSpec.until": _set(["stages", 0], {"name": "s", "until": BOOL, "passes": 2}),
    "StageSpec.passes": _set(["stages", 0, "passes"], INT),
    "StageSpec.quiet": _set(["stages", 0, "quiet"], TEXT),
    "StageSpec.max_actions": _set(["stages", 0, "max_actions"], INT),
    "StageSpec.max_calls": _set(["stages", 0, "max_calls"], INT),
    "StageSpec.brief": _set(["stages", 0, "brief"], TEXT),
    "ViewSpec.for_": _set(["views", "v", "for"], TEXT),
    "ViewSpec.title": _set(["views", "v", "title"], TEXT),
    "ViewSpec.of": _set(["views", "tokens", "of"], "$filter(token, $world.hid > 7)"),
    "ViewSpec.where": _set(["views", "tokens", "where"], BOOL),
    "ViewSpec.sort": _set(["views", "tokens", "sort"], "$world.hid * $it.v"),
    "ViewSpec.show": _set(["views", "v", "show"], TEXT),
    "ViewSpec.empty": _set(["views", "tokens"], {"of": "token", "where": "false", "show": "x", "empty": TEXT}),
    "ViewSpec.when": _set(["views", "v", "when"], BOOL),
    "ViewSpec.attach": _both(_set(["inputs", "pic"], {"type": "file", "source": "missing.png"}),
                             _set(["views", "v", "attach"], "'pic' if $world.hid > 7 else null")),
    "EventSpec.name": _set(["events"], [{"name": TEXT, "on": "round.start", "do": ["$world.pub += 1"]}]),
    "EventSpec.on": _set(["events"], [{"on": TEXT, "do": ["$world.pub += 1"]}]),
    "EventSpec.when": _set(["events"], [{"on": "round.start", "when": BOOL, "do": ["$entity('a').mine += 1"]}]),
    "EventSpec.do": _set(["events"], [{"on": "round.start", "do": ["$entity('a').mine += $world.hid"]}]),
    "EventSpec.say": _set(["events"], [{"on": "round.start", "say": TEXT}]),
    "OutputSpec.expr": _set(["outputs", "x"], NUM),
    "OutputSpec.type": _set(["outputs", "x"], {"expr": "1", "type": TEXT}),
    "OutputSpec.description": _set(["outputs", "x"], {"expr": "1", "description": TEXT}),
    "OutputSpec.unit": _set(["outputs", "x"], {"expr": "1", "unit": TEXT}),
    "OutputSpec.format": _set(["outputs", "x"], {"expr": "1", "format": TEXT}),
    "OutputSpec.series": _set(["outputs", "x"], {"expr": "1", "series": BOOL}),
    "EndSpec.when": _set(["end"], [{"when": "$round > 1 and " + BOOL}]),
    "EndSpec.name": _set(["end"], [{"when": "$round > 5", "name": TEXT}]),
    "EndSpec.winner": _set(["end"], [{"when": "$round > 1", "winner": ENTITY}]),
    "EndSpec.say": _set(["end"], [{"when": "$round > 1", "say": TEXT}]),
    "EndSpec.check": _set(["end"], [{"when": "$round > 5", "check": TEXT}]),
    "ArmSpec.description": _set(["arms", "arm"], {"description": TEXT}),
    "ArmSpec.inputs": _set(["arms", "arm"], {"inputs": {"i": NUM}}),
    "ArmSpec.patch": _set(["arms", "arm"], {"patch": {"description": TEXT}}),
    "InvariantSpec.expr": _set(["invariants"], [{"expr": "$world.hid > 0 or " + BOOL}]),
    "InvariantSpec.why": _set(["invariants"], [{"expr": "$world.pub >= 0", "why": TEXT}]),
    "InvariantSpec.check": _set(["invariants"], [{"expr": "true", "check": TEXT}]),
    "DefSpec.args": _set(["defs", "d"], {"args": [TEXT], "expr": "1"}),
    "DefSpec.expr": _both(_set(["defs", "d"], {"expr": NUM}), _set(["outputs", "y"], "$d")),
    "DefSpec.do": _both(_set(["defs", "d"], {"do": ["$entity('a').mine = $world.hid"]}),
                        _set(["events"], [{"on": "round.start", "do": [{"call": "d", "with": {}}]}])),
    "DefSpec.description": _set(["defs", "d"], {"expr": "1", "description": TEXT}),
}

#: Where to read the hidden value in each field of each core effect.
EFFECT_PLACES: dict[str, Callable[[dict[str, Any]], str]] = {
    "if.if": _effect({"if": BOOL, "then": ["$actor.mine += 1"]}, "if"),
    "if.then": _effect({"if": "true", "then": ["$actor.mine += $world.hid"]}, "then"),
    "if.else": _effect({"if": "false", "else": ["$actor.mine += $world.hid"]}, "else"),
    "each.each": _effect({"each": "[1] if $world.hid > 7 else [1, 2]", "do": ["$actor.mine += 1"]}, "each"),
    "each.where": _effect({"each": "p", "where": BOOL, "do": ["$it.mine += 1"]}, "where"),
    "each.do": _effect({"each": "p", "do": ["$it.mine += $world.hid"]}, "do"),
    "each.as": _effect({"each": "p", "as": TEXT, "do": []}, "as"),
    "each.sync": _effect({"each": "p", "sync": True, "do": ["$it.mine += $world.hid"]}, "sync"),
    "create.create": _effect({"create": TEXT}, "create"),
    "create.count": _effect({"create": "token", "count": INT}, "count"),
    "create.id": _effect({"create": "token", "id": "tok" + TEXT}, "id"),
    "create.name": _effect({"create": "token", "name": "T" + TEXT}, "name"),
    "create.props": _effect({"create": "token", "props": {"held": NUM}}, "props"),
    "create.at": _both(_set(["space"], {"grid": {"rows": 3, "cols": 3}}),
                       _effect({"create": "token", "at": "[$world.hid - 6, 0]"}, "at")),
    "create.as": _effect({"create": "token", "as": TEXT}, "as"),
    "remove.remove": _effect({"remove": "$entity('t1') if $world.hid > 7 else []"}, "remove"),
    "transfer.transfer": _effect({"transfer": TEXT, "from": "$actor", "to": "$entity('b')", "amount": 0},
                                 "transfer"),
    "transfer.from": _effect({"transfer": "k", "from": ENTITY, "to": "$entity('b')", "amount": 0}, "from"),
    "transfer.to": _effect({"transfer": "k", "from": "$actor", "to": ENTITY, "amount": 0}, "to"),
    "transfer.amount": _effect({"transfer": "k", "from": "$actor", "to": "$entity('b')",
                                "amount": "$world.hid - 7"}, "amount"),
    "transfer.into": _effect({"transfer": "k", "from": "$actor", "to": "$entity('b')", "amount": 0,
                              "into": TEXT}, "into"),
    "link.link": _effect({"link": TEXT, "from": "$actor", "to": "$entity('b')"}, "link"),
    "link.from": _effect({"link": "knows", "from": ENTITY, "to": "$entity('b')"}, "from"),
    "link.to": _effect({"link": "knows", "from": "$actor", "to": ENTITY}, "to"),
    "link.value": _effect({"link": "knows", "from": "$actor", "to": "$entity('b')", "value": NUM}, "value"),
    "link.props": _effect({"link": "knows", "from": "$actor", "to": "$entity('b')", "props": {"w": NUM}}, "props"),
    "unlink.unlink": _effect({"unlink": TEXT, "from": "$actor", "to": "$entity('b')"}, "unlink"),
    "unlink.from": _effect({"unlink": "knows", "from": ENTITY, "to": "$entity('b')"}, "from"),
    "unlink.to": _effect({"unlink": "knows", "from": "$actor", "to": ENTITY}, "to"),
    "move.move": _both(_set(["space"], {"grid": {"rows": 3, "cols": 3}}),
                       _effect({"move": ENTITY, "to": [1, 1]}, "move")),
    "move.to": _both(_set(["space"], {"grid": {"rows": 3, "cols": 3}}),
                     _effect({"move": "$actor", "to": "[$world.hid - 6, 0]"}, "to")),
    "post.post": _effect({"post": TEXT, "text": "x"}, "post"),
    "post.to": _effect({"post": "log", "text": "x", "to": ENTITY}, "to"),
    "post.author": _effect({"post": "log", "text": "x", "author": ENTITY}, "author"),
    "post.delay": _effect({"post": "log", "text": "x", "delay": "$world.hid - 7"}, "delay"),
    "post.drop": _effect({"post": "log", "text": "x", "drop": "($world.hid - 7) / 2"}, "drop"),
    "post.*": _effect({"post": "log", "text": "'x' + $text($world.hid)"}, "text"),
    "emit.emit": _effect({"emit": TEXT, "say": "x"}, "emit"),
    "emit.say": _effect({"emit": "news", "say": TEXT}, "say"),
    "emit.to": _effect({"emit": "news", "say": "x", "to": ENTITY}, "to"),
    "emit.data": _both(_set(["views", "v", "show"], "{$map($events(shock), $it.size)}"),
                       _effect({"emit": "shock", "say": "x", "data": {"size": NUM}}, "data")),
    "emit.delay": _effect({"emit": "news", "say": "x", "delay": "$world.hid - 7"}, "delay"),
    "emit.drop": _effect({"emit": "news", "say": "x", "drop": "($world.hid - 7) / 2"}, "drop"),
    "fail.fail": _effect({"fail": TEXT}, "fail"),
    "end.end": _effect({"end": TEXT}, "end", where="event"),
    "end.winner": _effect({"if": "$round > 1", "then": [{"end": "over", "winner": ENTITY}]}, "winner",
                          where="event"),
    "end.say": _effect({"end": "over", "say": TEXT}, "say", where="event"),
    "after.after": _effect({"after": "$world.hid - 6", "do": ["$world.pub += 1"]}, "after"),
    "after.do": _effect({"after": 1, "do": ["$entity('a').mine += $world.hid"]}, "do"),
    "wake.wake": _effect({"wake": ENTITY}, "wake"),
    "wake.why": _effect({"wake": "$entity('b')", "why": TEXT}, "why"),
    "wake.now": _effect({"wake": "$entity('b')", "now": BOOL}, "now"),
    "wake.actions": _effect({"wake": "$entity('b')", "now": True, "actions": [TEXT]}, "actions"),
    "repeat.repeat": _effect({"repeat": "$world.hid - 6", "do": ["$actor.mine += 1"]}, "repeat"),
    "repeat.while": _effect({"repeat": 3, "while": BOOL, "do": ["$actor.mine += 1"]}, "while"),
    "repeat.do": _effect({"repeat": 1, "do": ["$actor.mine += $world.hid"]}, "do"),
    "call.call": _effect({"call": TEXT}, "call"),
    "call.with": _both(_set(["defs", "d"], {"args": ["n"], "do": ["$entity('a').mine = $n"]}),
                       _effect({"call": "d", "with": {"n": NUM}}, "with")),
    "chance.chance": _effect({"chance": [{"p": "($world.hid - 6) / 4", "do": ["$actor.mine += 1"]},
                                         {"p": "1 - ($world.hid - 6) / 4", "do": []}]}, "chance"),
    "chance.outcomes": _effect({"chance": "d6", "outcomes": "[1] if $world.hid > 7 else [1, 2]", "as": "o",
                                "do": ["$actor.mine += $o"]}, "outcomes"),
    "chance.weight": _effect({"chance": "d6", "outcomes": [1, 2], "weight": "$world.hid - 6", "as": "o",
                              "do": ["$actor.mine += $o"]}, "weight"),
    "chance.as": _effect({"chance": "d6", "outcomes": [1, 2], "as": TEXT, "do": []}, "as"),
    "chance.do": _effect({"chance": "d6", "outcomes": [1, 2], "as": "o", "do": ["$actor.mine += $world.hid"]}, "do"),
}


#: More ways to read something hidden where the reader decides what may show: a message to one agent, a stage whose
#: actions nobody else learns of.
VARIANTS: dict[str, Callable[[dict[str, Any]], str]] = {
    "emit.say to another": _effect({"emit": "note", "to": "$entity('b')", "say": "a holds {$actor.mine}"}, "say"),
    "emit.say to its owner": _effect({"emit": "note", "to": "$actor", "say": "you hold {$actor.mine}"}, "say"),
    "emit.data to another": _both(_set(["views", "v", "show"], "{$map($events(note), $it.n)}"),
                                  _effect({"emit": "note", "to": "$entity('b')", "data": {"n": "$actor.mine"}},
                                          "data")),
    "post.* to its author": _effect({"post": "log", "to": "$actor", "text": "'mine ' + $text($actor.mine)"},
                                    "text"),
    "wake.why to another": _effect({"wake": "$entity('b')", "why": "a holds {$actor.mine}"}, "why"),
    "StageSpec.who unannounced": _both(_set(["actions", "go", "announce"], False),
                                       _set(["stages", 0, "who"], "$world.hid > 7 or $it.id == 'b'")),
    "ViewSpec.show of an item": _set(["views", "tokens"], {"of": "token", "show": "{name} {held}"}),
    "ActionSpec.outcome own": _set(["actions", "go", "outcome"], "You hold {$actor.mine}."),
}


def test_every_field_has_a_placement():
    assert set(PLACES) == set(FIELDS)
    assert set(EFFECT_PLACES) == {f"{op}.{key}" for op, fields in EFFECTS.items() for key in fields}


def _worlds(field: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """The first world and the second, which differ only in what no agent may know (the world's ``hid``, and ``a``'s
    own ``mine``), with a read of it in ``field``; where it is written."""
    place = PLACES.get(field) or EFFECT_PLACES.get(field) or VARIANTS[field]
    first = base()
    path = place(first)
    second = copy.deepcopy(first)
    second["world"]["hid"]["default"] = HIDDEN[1]
    second["entities"]["a"].setdefault("props", {}).setdefault("mine", 5)
    return first, second, path


def _refused_at(issues: list[Any], path: str) -> bool:
    """Whether ``check`` refused the contract at ``path`` (or inside it)."""
    return any(issue.severity == "error" and (issue.path == path or issue.path.startswith((path + ".", path + "[")))
               for issue in issues)


@pytest.mark.parametrize("field", sorted({*PLACES, *EFFECT_PLACES, *VARIANTS}))
def test_a_hidden_read_in_any_field_is_refused_by_the_check_or_reaches_no_other_agent(field):
    first, second, path = _worlds(field)
    issues = fg_env.check(first, rounds=0)
    if _refused_at(issues, path):
        return  # the check refuses it where it is written: load refuses it too
    errors = [f"{issue.path}: {issue.message}" for issue in issues if issue.severity == "error"]
    if errors:
        pytest.fail(f"the placement does not check clean elsewhere: {errors[0]}")  # a placement to fix
    policies = {"p": "policy:x"} if field.startswith("PolicyRule.") else None
    for observer in ("b",) if field in VARIANTS else ("a", "b"):  # `a`'s own is its to read
        one = play(first, 1, observer, participants=policies)
        assert one.status in ("completed", "ended"), (field, observer, one.status)
        other = play(second, 1, observer, replay=one, perturb=False, participants=policies)
        assert other.status in ("completed", "ended"), (field, observer, other.status)  # how it ends is the rules'
        assert not leaks(one, other, observer), (field, leaks(one, other, observer))
    for world in (first, second):
        result = fg_env.load(world, seed=1).run(policies or "random")
        assert result.status in ("completed", "ended"), (field, result.error)  # the run refuses nothing the check let
