"""The world's small parts: record entries, log events, the journal, and the `$physics` and `$clock` views
expressions read (the `$world` view is :class:`fg_env.expr.objects.PropsView`)."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..expr import ExprError

if TYPE_CHECKING:
    from ..contract import Contract
    from .live import SdkWorld

__all__ = ["Entry", "LogEvent", "Journal", "PhysicsView", "ClockView", "private_metrics"]

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
    """One record entry. ``author`` reads as the authoring entity."""

    world: SdkWorld

    def expr_attr(self, name: str, source: str | None) -> Any:
        if name == "author":
            author = self.get("author")
            return self.world.entities.get(author) if author else None
        if name in self:
            return self[name]
        raise ExprError(f"record entry has no field '{name}' (fields: {', '.join(sorted(self))})", source)


@dataclass(eq=False, slots=True)
class LogEvent:
    """Something that happened, in order. ``to`` None means every agent may learn of it."""

    seq: int
    round: int
    kind: str
    text: str = ""
    actor: str | None = None
    to: tuple[str, ...] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    stage: str | None = None
    #: Clock time when it happened (continuous clock only).
    time: float | None = None

    def visible_to(self, entity_id: str) -> bool:
        return self.to is None or entity_id in self.to

    def expr_attr(self, name: str, source: str | None) -> Any:
        if name in ("seq", "round", "kind", "text", "actor", "stage", "time"):
            return getattr(self, name)
        if name in self.data:
            return self.data[name]
        raise ExprError(f"event has no field '{name}'", source)

    def to_dict(self) -> dict[str, Any]:
        out = {"seq": self.seq, "round": self.round, "kind": self.kind}
        for key in ("text", "actor", "stage"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.to is not None:
            out["to"] = list(self.to)
        if self.data:
            out["data"] = self.data
        if self.time is not None:
            out["time"] = self.time
        return out


class PhysicsView:
    """``$physics`` — current values of physics variables and params."""

    def __init__(self, world: SdkWorld):
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
    def __init__(self, world: SdkWorld):
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
        if name == "time":
            return w.now()
        if name == "horizon":
            return w.horizon
        raise ExprError(f"clock has no field '{name}' (round, rounds, left, unit, date, start, label, time, horizon)",
                        source)


class Journal:
    def __init__(self) -> None:
        self._undo: list[Callable[[], object]] = []
        #: Bumped by every change and every undo: equal versions mean an unchanged world.
        self.version = 0
        #: Open :meth:`held` blocks, and whether a :meth:`clear` inside them waits for them to finish.
        self.holding = 0
        self._clear_due = False

    def mark(self) -> int:
        return len(self._undo)

    def push(self, undo: Callable[[], object]) -> None:
        self._undo.append(undo)
        self.version += 1

    def rollback(self, mark: int) -> None:
        while len(self._undo) > mark:
            self._undo.pop()()
            self.version += 1

    def clear(self) -> None:
        if self.holding:
            self._clear_due = True
            return
        self._undo.clear()
        self._clear_due = False

    @contextmanager
    def held(self) -> Iterator[None]:
        """Keep every change made inside the block undoable until it ends: commits inside it (an agent's action and
        the triggers it sets off) clear the journal only once the block finishes without an error, so a failure
        anywhere in it can still undo all of it."""
        self.holding += 1
        try:
            yield
        except BaseException:
            self.holding -= 1
            if not self.holding:
                self._clear_due = False  # the caller undoes the block instead
            raise
        self.holding -= 1
        if not self.holding and self._clear_due:
            self.clear()
