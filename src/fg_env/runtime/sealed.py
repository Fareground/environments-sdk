"""Committing one agent's sealed choices, once everyone in a simultaneous stage has chosen.

The schedule decides the order the agents' choices commit in (:mod:`.schedule`); here each agent's choices commit one
at a time, in the order the agent made them, each checked again against the world as it now is — an earlier agent may
have taken what a choice counted on. A choice refused then is told to its agent as an outcome (and to the run's
facts); a rule that fails or an invariant it breaks refuses that choice alone. In a stage with `valid` rules an
agent's choices commit or are undone as a whole.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .facts import CommitRefused, Committed, Undone

if TYPE_CHECKING:
    from .diagnosis import SealedWrites
    from .rules import Rules
    from .turn import Turn

__all__ = ["commit_sealed"]


def commit_sealed(rules: Rules, turn: Turn, writes: SealedWrites, atomic: bool) -> bool | None:
    """Commit ``turn``'s sealed choices through ``rules``, noting their writes in ``writes``; ``atomic`` (a stage with
    `valid` rules) commits or undoes them as a whole. Whether the agent's choices stand, or None when the run ended
    while they committed (nothing more commits)."""
    mark = rules.world.mark() if atomic else None
    applied = 0
    writes.writer = turn.actor.name or turn.actor.id
    for name, args in turn.ledger.intents:
        if rules.ended() and mark is None:
            return None
        writes.action = name
        applied += _commit_intent(rules, turn, name, args, deferred=mark is not None)
    if mark is None:
        return bool(turn.ledger.intents)
    acted = _settle(rules, turn, mark, applied)
    return None if rules.ended() else acted


def _settle(rules: Rules, turn: Turn, mark: int, applied: int) -> bool:
    """An atomic simultaneous stage: keep one agent's committed choices when they meet `valid`, else undo
    them all and tell the agent why — also when a rule fails or an invariant breaks as they commit. True when
    the agent's choices stand."""
    world, stage = rules.world, turn.stage

    def commit() -> str | None:
        with world.luck.turn_context(None, turn.ledger.pending):
            why = rules.invalid(turn.actor, stage) if applied else None
        if why is None:
            rules.commit(f"stages.{stage.name}")
        return why

    with rules.gate:
        why, fault = rules.guarded(commit, mark)
        if fault is not None:
            why = fault
        if why is None:
            rules.react(stage)
            return bool(turn.ledger.intents)
        world.rollback(mark)
        world.emit("outcome", f"Your choices were undone: {why}.", actor=turn.actor.id, to=(turn.actor.id,),
                   data={"ok": False, "undone": True})
        world.commit()
        rules.facts.emit(Undone(applied, fault is not None), turn)
    return False


def _commit_intent(rules: Rules, turn: Turn, name: str, args: dict[str, Any], deferred: bool) -> int:
    """Apply one sealed choice; 1 when it applied. ``deferred`` (atomic stages) leaves the commit to the
    whole turn's settling. A rule that fails or an invariant it breaks refuses the choice alone."""
    actor, world = turn.actor, rules.world
    with rules.gate:
        applied, fault = rules.guarded(lambda: _apply_intent(rules, turn, name, args, deferred), action=name)
        if applied is None:
            assert fault is not None
            world.emit("outcome", f"Your {name.replace('_', ' ')} did not happen: {fault}.",
                       actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
            rules.facts.emit(CommitRefused(name, fault, faulted=True), turn)
            if not deferred:
                world.commit()
            return 0
        if applied and not deferred:
            rules.react(turn.stage)
        return applied


def _apply_intent(rules: Rules, turn: Turn, name: str, args: dict[str, Any], deferred: bool) -> int:
    actor, world = turn.actor, rules.world
    blocked = rules.blocked(actor, name, {}, {}) if actor.alive else "you are no longer active"
    params, problem = ({}, blocked) if blocked else rules.validate(actor, name, args)
    verb = name.replace("_", " ")
    if problem:
        world.emit("outcome", f"Your {verb} did not happen: {str(problem).rstrip('.')}.",
                   actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
        rules.facts.emit(CommitRefused(name, str(problem)), turn)
        if not deferred:
            world.commit()
        return 0
    outcome = rules.apply(actor, name, params)
    text = outcome.text if outcome.ok else f"Your {verb} failed: {outcome.text}"
    data = {"action": name, "ok": outcome.ok, **({"assets": outcome.assets} if outcome.assets else {})}
    world.emit("outcome", text, actor=actor.id, to=(actor.id,), data=data)
    if not outcome.ok:
        rules.facts.emit(CommitRefused(name, outcome.text), turn)
        if not deferred:
            world.commit()
        return 0
    if not deferred:
        rules.commit(f"actions.{name}")
    rules.facts.emit(Committed(name), turn)
    return 1
