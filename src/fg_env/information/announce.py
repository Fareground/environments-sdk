"""What an action's announcement may repeat of its arguments.

Every agent reads an action's announcement, so it repeats no argument the action keeps from some of them: none carried
into a record entry that is not broadcast to everyone (a record that does not notify, a directed or restricted entry),
none at all of an action whose effects may write a private property (an argument can decide such a write through a
condition, a key or a transfer as surely as by being copied into it), and none at all of a simultaneous stage's sealed
choices — a losing sealed bid stays sealed unless the action's own `announce` says otherwise.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..contract import Contract, DefSpec, RecordSpec
from ..effects.statements import compile_statement
from ..expr import ExprError
from ..world.store import World
from ..world.values import plain_value

__all__ = ["Redaction", "notified_since"]


class Redaction:
    """The arguments each action's announcement may repeat (the private-property analysis of its effects is worked
    out once per action)."""

    def __init__(self, contract: Contract):
        self.contract = contract
        #: Per action, whether its effects may write a private property.
        self._writes_private: dict[str, bool] = {}

    def public_params(self, world: World, name: str, params: dict[str, Any], record_mark: int) -> dict[str, Any]:
        """The arguments of action ``name`` its announcement may repeat, the entries it posted being those after
        ``record_mark``."""
        if self._sealed(world):
            return {}
        if self._keeps_secrets(world, name):
            return {}
        posted = [(spec, entry) for _, spec, entry in self._posted_since(world, record_mark)]
        return _public_params(params, posted)

    def restricted_since(self, world: World, record_mark: int) -> list[list[Any]]:
        """``[record, seq]`` of each entry posted after ``record_mark`` that not every agent may see (directed, or
        its record's `visible` is a rule). The action that posted them carries them, and is seen by exactly the
        readers who may see them all (see :meth:`~fg_env.world.evaluation.Evaluation.event_visible`)."""
        return [[name, entry["seq"]] for name, spec, entry in self._posted_since(world, record_mark)
                if entry.get("to") is not None or spec.visible != "all"]

    def _posted_since(self, world: World, record_mark: int) -> list[tuple[str, RecordSpec, dict[str, Any]]]:
        """Entries posted after ``record_mark``, with their record's name and spec."""
        if world.record_seq == record_mark:
            return []
        posted: list[tuple[str, RecordSpec, dict[str, Any]]] = []
        for name, spec in self.contract.records.items():
            for entry in reversed(world.records_store.get(name, [])):
                if entry["seq"] <= record_mark:
                    break
                posted.append((name, spec, entry))
        return posted

    def _sealed(self, world: World) -> bool:
        """Whether actions now commit as a simultaneous stage's sealed choices: announced without their arguments,
        so a losing sealed bid stays sealed unless the action's `announce` says otherwise."""
        stage = world.stage
        return any(spec.name == stage and spec.turns == "simultaneous" for spec in self.contract.stage_list())

    def _keeps_secrets(self, world: World, name: str) -> bool:
        """Whether action ``name`` may write a private property: then it announces none of its arguments."""
        known = self._writes_private.get(name)
        if known is None:
            known = _writes_private(world.private_names, self.contract.actions[name].do, self.contract.defs)
            self._writes_private[name] = known
        return known


def _public_params(params: dict[str, Any], posted: Sequence[tuple[RecordSpec, dict[str, Any]]]) -> dict[str, Any]:
    """The arguments an announcement may repeat. An entry that is not broadcast to everyone
    (a record that does not notify, a directed or restricted entry) keeps its content to
    its own audience, so arguments carried into it are left out."""
    kept = [entry.get(field) for spec, entry in posted
            if not spec.notify or entry.get("to") is not None or spec.visible != "all"
            for field in spec.fields]
    if not kept:
        return params
    return {k: v for k, v in params.items() if not _carried(plain_value(v), kept)}


def _writes_private(private: frozenset[str], effects: Any, defs: Mapping[str, DefSpec],
                    called: frozenset[str] = frozenset()) -> bool:
    """Whether ``effects`` may write a property named in ``private``, on any branch and at any depth: an assignment
    into one (whatever its key), a transfer of or into one, a created entity's, or a def's body it calls. Control
    flow carries a value as surely as a copy does, so this asks whether the write can happen, not what it copies."""
    if isinstance(effects, str):
        try:
            statement = compile_statement(effects)
        except ExprError:
            return False  # a condition or a text, not an assignment
        return statement.local is None and any(kind == "field" and step in private for kind, step in statement.steps)
    if isinstance(effects, list):
        return any(_writes_private(private, item, defs, called) for item in effects)
    if not isinstance(effects, dict):
        return False
    if "transfer" in effects and (effects["transfer"] in private or effects.get("into") in private):
        return True
    if "create" in effects and isinstance(effects.get("props"), dict) and set(effects["props"]) & private:
        return True
    name = effects.get("call")
    if isinstance(name, str) and name not in called:
        spec = defs.get(name)
        if spec is not None and spec.do is not None and _writes_private(private, spec.do, defs, called | {name}):
            return True
    return any(_writes_private(private, value, defs, called) for value in effects.values())


def _carried(value: Any, fields: Sequence[Any]) -> bool:
    """True when an argument value (or text containing it) is stored in one of ``fields``."""
    if value is None or isinstance(value, bool) or value == "":
        return False
    for stored in fields:
        if stored == value:
            return True
        if isinstance(value, str) and isinstance(stored, str) and value in stored:
            return True
        if isinstance(stored, (list, tuple)) and any(_carried(value, [item]) for item in stored):
            return True
    return False


def notified_since(world: World, log_mark: int) -> bool:
    """True when a record entry was delivered as news after log position ``log_mark``."""
    for event in reversed(world.log):
        if event.seq <= log_mark:
            return False
        if event.kind == "record":
            return True
    return False

