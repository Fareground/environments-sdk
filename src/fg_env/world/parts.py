"""The world's small parts: record entries, log events, and the `$physics` and `$clock` views
expressions read (the `$world` view is :class:`fg_env.expr.objects.PropsView`)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..expr import ExprError

if TYPE_CHECKING:
    from ..contract import Contract
    from .store import World

__all__ = ["Entry", "LogEvent", "PhysicsView", "ClockView", "private_metrics"]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def private_metrics(contract: Contract, private: frozenset[str]) -> frozenset[str]:
    """The series outputs worked out from agents' private properties: those whose sampled expression — or a def or
    output it reads — names one (``private``, the names agent types keep private). Read by name, so an output that
    only might read one counts too: showing it to agents is refused, and an output that must be shown reads no
    private name."""
    if not private:
        return frozenset()
    sampled = {name: spec.sampled or "" for name, spec in contract.series_outputs().items()}
    texts = dict(sampled)
    texts.update({name: spec.expr or "" for name, spec in contract.expr_defs().items() if name not in texts})
    names = {name: set(_NAME.findall(text)) for name, text in texts.items()}
    hidden = {name for name, found in names.items() if found & private}
    grown = True
    while grown:  # an output or def reading one that is worked out from private properties is too
        more = {name for name, found in names.items() if name not in hidden and found & hidden}
        hidden |= more
        grown = bool(more)
    return frozenset(hidden & set(sampled))


class Entry(dict):
    """One record entry. ``author`` reads as the authoring entity.

    Its ``seq`` is the world's count of every entry posted to every record, for game logic. What an agent reads is a
    copy numbered in that agent's own view of what it read (:meth:`numbered`, the rule events follow too), so no
    reader learns from its ``seq`` how many entries it cannot see; :attr:`key` is the world's number in either."""

    world: World
    _key: int | None = None

    @property
    def key(self) -> int:
        """The entry's number among every entry posted: the same for every reader of it."""
        return self._key if self._key is not None else self["seq"]

    def numbered(self, position: int) -> Entry:
        """The entry as a reader sees it: numbered by its ``position`` (from 1) among the entries that reader sees."""
        if position == self["seq"]:
            return self
        copy = Entry(self)
        copy.world, copy._key = self.world, self.key
        copy["seq"] = position
        return copy

    def expr_attr(self, name: str, source: str | None) -> Any:
        if name == "author":
            author = self.get("author")
            return self.world.entities.get(author) if author else None
        if name in self:
            return self[name]
        raise ExprError(f"record entry has no field '{name}' (fields: {', '.join(sorted(self))})", source)


@dataclass(eq=False, slots=True)
class LogEvent:
    """Something that happened, in order. ``to`` None means every agent may learn of it.

    Its ``seq`` is the world's count of every event logged, for game logic; what an agent reads is a copy numbered in
    that agent's own view of the events it read (:meth:`numbered`), as a record entry is, so no reader learns from its
    ``seq`` how many events it may not know of. :attr:`key` is the world's number in either."""

    seq: int
    round: int
    kind: str
    text: str = ""
    actor: str | None = None
    to: tuple[str, ...] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    stage: str | None = None
    _key: int | None = field(default=None, repr=False)

    @property
    def key(self) -> int:
        """The event's number among every event logged: the same for every reader of it."""
        return self._key if self._key is not None else self.seq

    def numbered(self, position: int) -> LogEvent:
        """The event as a reader sees it: numbered by its ``position`` (from 1) among the events that reader read."""
        if position == self.seq:
            return self
        return LogEvent(position, self.round, self.kind, self.text, self.actor, self.to, self.data, self.stage,
                        self.key)

    def visible_to(self, entity_id: str) -> bool:
        return self.to is None or entity_id in self.to

    def expr_attr(self, name: str, source: str | None) -> Any:
        if name in ("seq", "round", "kind", "text", "actor", "stage"):
            return getattr(self, name)
        if name in self.data:
            return self.data[name]
        raise ExprError(f"event has no field '{name}'", source)

    def to_dict(self) -> dict[str, Any]:
        out = {"seq": self.seq, "round": self.round, "kind": self.kind, "text": self.text}
        for key in ("actor", "stage"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.to is not None:
            out["to"] = list(self.to)
        if self.data:
            out["data"] = self.data
        return out


class PhysicsView:
    """``$physics`` — current values of physics variables and params."""

    ROOT = "physics"

    def __init__(self, world: World):
        self._world = world

    def expr_attr(self, name: str, source: str | None) -> Any:
        model = self._world.physics
        if model is None:
            raise ExprError("this environment declares no physics", source)
        if name in model.variables:
            return model.variables[name].value
        if name in model.params:
            return model.params[name]
        raise ExprError(f"physics has no variable or param '{name}'", source)


class ClockView:
    ROOT = "clock"

    def __init__(self, world: World):
        self._world = world

    def expr_attr(self, name: str, source: str | None) -> Any:
        w = self._world
        if name == "round":
            return w.round
        if name == "rounds":
            return w.rounds
        if name == "unit":
            return w.contract.clock.unit
        if name == "left":
            return max(0, w.rounds - w.round)
        if name == "date":
            return w.date()
        if name == "start":
            return w.start
        if name == "label":
            return w.clock_label()
        raise ExprError(f"clock has no field '{name}' (round, rounds, left, unit, date, start, label)", source)
