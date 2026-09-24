"""What an action's announcement may repeat of its arguments.

Every agent reads an action's announcement, so it repeats no argument the action keeps from some of them: none carried
into a record entry that is not broadcast to everyone (a record that does not notify, a directed or restricted entry),
none its effects write into a private property, and none at all of a simultaneous stage's sealed choices — a losing
sealed bid stays sealed unless the action's own `announce` says otherwise.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from ..contract import Contract, RecordSpec
from ..effects.statements import compile_statement
from ..expr import ExprError
from ..world.live import SdkWorld, _plain

__all__ = ["Redaction", "notified_since"]


class Redaction:
    """The arguments each action's announcement may repeat (the private-property analysis of its effects is worked
    out once per action)."""

    def __init__(self, contract: Contract):
        self.contract = contract
        #: Per action, the arguments its effects write into a private property.
        self._kept_secrets: dict[str, frozenset[str]] = {}

    def public_params(self, world: SdkWorld, name: str, params: dict[str, Any], record_mark: int) -> dict[str, Any]:
        """The arguments of action ``name`` its announcement may repeat, the entries it posted being those after
        ``record_mark``."""
        if self._sealed(world):
            return {}
        return _public_params(params, self._posted_since(world, record_mark), self._kept_secret(world, name))

    def _posted_since(self, world: SdkWorld, record_mark: int) -> list[tuple[RecordSpec, dict[str, Any]]]:
        """Entries posted after ``record_mark``, with their record's spec."""
        if world._record_seq == record_mark:
            return []
        posted: list[tuple[RecordSpec, dict[str, Any]]] = []
        for name, spec in self.contract.records.items():
            for entry in reversed(world.records_store.get(name, [])):
                if entry["seq"] <= record_mark:
                    break
                posted.append((spec, entry))
        return posted

    def _sealed(self, world: SdkWorld) -> bool:
        """Whether actions now commit as a simultaneous stage's sealed choices: announced without their arguments,
        so a losing sealed bid stays sealed unless the action's `announce` says otherwise."""
        stage = world.stage
        return any(spec.name == stage and spec.turns == "simultaneous" for spec in self.contract.stage_list())

    def _kept_secret(self, world: SdkWorld, name: str) -> frozenset[str]:
        """The arguments of action ``name`` that its effects write into a private property."""
        known = self._kept_secrets.get(name)
        if known is None:
            spec = self.contract.actions[name]
            known = frozenset(_written_into(world.private_names, [spec.do]))
            self._kept_secrets[name] = known
        return known


def _public_params(params: dict[str, Any], posted: Sequence[tuple[RecordSpec, dict[str, Any]]],
                   secret: frozenset[str]) -> dict[str, Any]:
    """The arguments an announcement may repeat. An entry that is not broadcast to everyone
    (a record that does not notify, a directed or restricted entry) keeps its content to
    its own audience, so arguments carried into it are left out; so are ``secret`` ones, which the action keeps
    in a private property."""
    kept = [entry.get(field) for spec, entry in posted
            if not spec.notify or entry.get("to") is not None or spec.visible != "all"
            for field in spec.fields]
    if not kept and not secret:
        return params
    return {k: v for k, v in params.items() if k not in secret and not _carried(_plain(v), kept)}


def _written_into(private: frozenset[str], effects: Any) -> Iterator[str]:
    """The arguments (``$params.<name>``) whose value may reach a property named in ``private`` through the
    assignments in ``effects``, however nested: read on the right of an assignment into one, or carried there by
    locals (``$x = $params.v``, then ``$actor.secret = $x``). It follows the value, not the wording."""
    statements = list(_statements(effects))
    carried: dict[str, set[str]] = {}  # local → the arguments its value may hold

    def reads(statement: Any) -> set[str]:
        found = {chain[1] for chain in statement.value.paths if chain[0] == "params" and len(chain) > 1}
        return found.union(*(carried.get(root, ()) for root in statement.value.roots))

    changed = True
    while changed:  # a local may take its value from one set later in the list (in a loop): follow to a fixed point
        changed = False
        for statement in statements:
            if statement.local is not None:
                held = carried.setdefault(statement.local, set())
                grown = reads(statement) - held
                if grown:
                    held |= grown
                    changed = True
    for statement in statements:
        if statement.local is None and any(kind == "field" and step in private for kind, step in statement.steps):
            yield from reads(statement)


def _statements(effects: Any) -> Iterator[Any]:
    """Every assignment statement in ``effects``, however nested, in order."""
    if isinstance(effects, str):
        try:
            yield compile_statement(effects)
        except ExprError:
            return  # a condition or a text, not an assignment
    elif isinstance(effects, (list, dict)):
        for item in effects.values() if isinstance(effects, dict) else effects:
            yield from _statements(item)


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


def notified_since(world: SdkWorld, log_mark: int) -> bool:
    """True when a record entry was delivered as news after log position ``log_mark``."""
    for event in reversed(world.log):
        if event.seq <= log_mark:
            return False
        if event.kind == "record":
            return True
    return False

