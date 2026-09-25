"""The shape of every field of every effect operation: the one table the checker and the runner share.

A field holds one of a few kinds of JSON value (a name, a template text, an object, a condition, what a loop goes
over, an effect list, a whole number or an expression …); :func:`misshapen` lists the fields of an operation object
that hold something else. ``null`` fits only a field that takes any value: a field left out is written by leaving it
out. The checker reports every misshapen field at its path; the runner reads a condition only in a shape
:data:`CONDITION` fits, so a value the checker would refuse is refused at run time too, never taken as true or false.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["CONDITION", "EFFECT_FIELDS", "READ_ONLY", "Shape", "misshapen"]

#: Why no rule assigns an entity's built-in fields (id, name, type, alive, at), and what changes them instead.
READ_ONLY = ("`create` gives the id and name, `move` changes at and `remove` ends alive; keep anything else in a "
             "declared property")


@dataclass(frozen=True)
class Shape:
    """The kinds of JSON value a field may hold, and how its messages name them (no kinds: any value, null too)."""

    word: str
    kinds: tuple[type, ...] | None = None

    def fits(self, value: Any) -> bool:
        if self.kinds is None:
            return True
        if isinstance(value, bool):  # a bool is an int to Python, never to a contract
            return bool in self.kinds
        return isinstance(value, self.kinds)


NAME = Shape("a name (text)", (str,))
TEXT = Shape("text", (str,))
OBJECT = Shape("an object", (Mapping,))
DATA = Shape("an object or an expression", (Mapping, str))
#: An `if`, `where`, `while` or `now`: an expression text, or a constant.
CONDITION = Shape("a condition (an expression text, or true / false)", (str, bool))
#: What an `each` goes over.
ITEMS = Shape("a type name, an expression or a list", (str, list))
EFFECTS = Shape("an effect list (or one effect)", (str, Mapping, list))
ROUNDS = Shape("a whole number of rounds ≥ 1, or an expression", (int, str))
COUNT = Shape("a whole number ≥ 0, or an expression", (int, str))
CHANCE = Shape("a probability from 0 to 1, or an expression", (int, float, str))
#: A delivery's delay: its range (whole rounds ≥ 0) is the delivery check's to say.
DELAY = Shape("a number of rounds, or an expression", (int, float, str))
FLAG = Shape("true or false", (bool,))
NAMES = Shape("a list of names", (list,))
BRANCHES = Shape("a list of branches, or the name of a draw", (list, str))
VALUE = Shape("a value")

#: Every core operation's fields and their shapes (a `post` also takes its record's fields, each any value).
EFFECT_FIELDS: dict[str, dict[str, Shape]] = {
    "if": {"if": CONDITION, "then": EFFECTS, "else": EFFECTS},
    "each": {"each": ITEMS, "where": CONDITION, "do": EFFECTS, "as": NAME, "sync": FLAG},
    "create": {"create": NAME, "count": COUNT, "id": TEXT, "name": TEXT, "props": OBJECT, "at": VALUE, "as": NAME},
    "remove": {"remove": VALUE},
    "transfer": {"transfer": NAME, "from": VALUE, "to": VALUE, "amount": VALUE, "into": NAME},
    "link": {"link": NAME, "from": VALUE, "to": VALUE, "value": VALUE, "props": OBJECT},
    "unlink": {"unlink": NAME, "from": VALUE, "to": VALUE},
    "move": {"move": VALUE, "to": VALUE},
    "post": {"post": NAME, "to": VALUE, "author": VALUE, "delay": DELAY, "drop": CHANCE},
    "emit": {"emit": NAME, "say": TEXT, "to": VALUE, "data": DATA, "delay": DELAY, "drop": CHANCE},
    "fail": {"fail": TEXT},
    "end": {"end": TEXT, "winner": VALUE, "say": TEXT},
    "after": {"after": ROUNDS, "do": EFFECTS},
    "wake": {"wake": VALUE, "why": TEXT, "now": CONDITION, "actions": NAMES},
    "repeat": {"repeat": COUNT, "while": CONDITION, "do": EFFECTS},
    "call": {"call": NAME, "with": OBJECT},
    "chance": {"chance": BRANCHES, "outcomes": VALUE, "weight": VALUE, "as": NAME, "do": EFFECTS},
}


def misshapen(op: str, effect: Mapping[str, Any]) -> list[tuple[str, Shape]]:
    """The fields of ``effect`` (an object naming the core operation ``op``) whose value does not fit their shape."""
    fields = EFFECT_FIELDS[op]
    return [(key, shape) for key, value in effect.items()
            if (shape := fields.get(key)) is not None and not shape.fits(value)]
