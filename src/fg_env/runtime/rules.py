"""The rules of a run: the one place world logic is evaluated and committed.

Every change the rules make — an event's `do`, a scheduled effect, a feed, a delivered message — is one undoable block
(:meth:`Rules.run_block`) that commits through :meth:`Rules.commit`: the invariants due, the end conditions checked
on every commit, and the `change` events the commit sets off. An agent's action is checked, applied and tried here too;
a rule that fails inside it refuses that action alone (:meth:`Rules.guarded`, see :mod:`fg_env.actions.faults`). The
events of each anchor run through :class:`~fg_env.runtime.events.Events`. When an anchor fires, who acts, and when
reactions are played is the schedule's (:mod:`fg_env.runtime.schedule`), never the rules'.

Invariants and end conditions are checked at set moments. An invariant's `check` and an end condition's `check` choose
how often: ``action`` also checks the moment anything commits (an action, a sealed choice, an effect block).

An invariant that asks the same of every member of a type — ``$all(<type>, <condition>)``, alone or joined by ``and``,
whose condition reads only the member's own properties and ``$inputs`` — is checked after a commit only for the
members created or changed since it last held, so an action costs the same however large the crowd.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from ..actions.book import ACTION_BUDGET, ActionBook, Outcome
from ..actions.faults import fault_reason, world_logic_refused
from ..contract import Contract, StageSpec
from ..effects.runner import EffectRunner
from ..errors import FatalRunError, InvariantViolation, RunError
from ..expr import EVERYONE, ExprError, Scope, compile_expr, item_conditions, shared_budget, truthy
from ..expr.objects import Entity
from ..expr.template import compile_template
from ..world.live import Abort, OutOfBounds, _plain
from .events import Events

if TYPE_CHECKING:
    from ..world.live import SdkWorld
    from .diagnosis import Diagnosis
    from .state import RunState

__all__ = ["Rules"]

T = TypeVar("T")

#: The moments each `invariants[].check` setting is checked at.
_INVARIANT_MOMENTS = {"action": ("build", "action", "round"), "round": ("build", "round"), "end": ("end",)}


def _no_reactions(stage: StageSpec | None) -> None:
    """Until the run is wired, a commit has no one to hand reactions to."""


class Rules:
    """The rules of one run over its world. ``lock`` is the run's lock: every change is made holding it."""

    def __init__(self, contract: Contract, world: SdkWorld, effects: EffectRunner, actions: ActionBook,
                 state: RunState, diagnosis: Diagnosis, lock: threading.RLock):
        self.contract = contract
        self.world = world
        self.effects = effects
        self.actions = actions
        self.state = state
        self.diagnosis = diagnosis
        self.lock = lock
        #: Plays the reactions a commit asked for, once nothing can undo it (the schedule's; wired by the run).
        self.react: Callable[[StageSpec | None], None] = _no_reactions
        self.end_on_action = any(end.check == "action" for end in contract.end)
        self.events = Events(self)

    # -- where the run is ----------------------------------------------------------------------------------------

    def ended(self) -> bool:
        """Whether the end of the run has been requested."""
        return self.world.end_request is not None

    def stage_spec(self) -> StageSpec | None:
        """The stage being played, or None between stages."""
        name = self.world.stage
        return next((s for s in self.contract.stage_list() if s.name == name), None) if name else None

    # -- changes ---------------------------------------------------------------------------------------------------

    def run_block(self, effects: list[Any], vars: dict[str, Any], path: str, *, owner: Any = None,
                  luck: str | None = None, check: bool = True) -> None:
        """Apply ``effects`` as one undoable block of world logic: a refusal in it (a `fail`, a transfer or write that
        does not fit) fails the run — or, inside an agent's action, refuses that action. ``check=False``: one item of a
        block whose invariants are checked once it is whole (a round event's `each`), unless a `change` event fires or
        an agent reacts first. The block draws from the stream of ``luck`` (default: its path) and ``owner`` (default:
        its $actor), so an entity's luck does not shift when others come or go."""
        if not effects:
            return
        world = self.world
        with self.lock, world.luck.at(luck or path, vars.get("actor") if owner is None else owner):
            mark = world.journal.mark()
            try:
                with shared_budget(ACTION_BUDGET, path):
                    self.effects.run(effects, dict(vars), path)
            except OutOfBounds as refusal:
                world.journal.rollback(mark)
                raise RunError(f"{refusal.reason} Keep it in range where it is written, e.g. with "
                               "$clamp(x, low, high), or guard the write with an `if`", path) from None
            except Abort as refusal:
                world.journal.rollback(mark)
                raise RunError(world_logic_refused(refusal.reason), path) from None
            except BaseException:
                world.journal.rollback(mark)
                raise
            self.commit(path, check)
            self.react(self.stage_spec())

    def commit(self, path: str, check: bool = True) -> None:
        """A change at ``path`` has applied: check the invariants (unless ``check`` is false and no agent is waiting to
        react) and the end conditions checked on every commit, keep the change, and fire the `change` events it set
        off."""
        if check or self.world.reactions:
            self.check_invariants(path)
        if self.end_on_action:
            self.check_end("action")
        self.world.journal.clear()
        self.check_changes(path)

    def guarded(self, work: Callable[[], T], mark: int | None = None,
                action: str | None = None) -> tuple[T | None, str | None]:
        """``(work(), None)``; or, when a rule fails or an invariant breaks inside it, ``(None, reason)`` with
        everything it changed undone — back to ``mark`` when given (where an atomic turn's actions began) — and the
        failure counted for the run's diagnostics, against the contract ``action`` being applied when given.
        ``reason`` is safe to show the agent (see :mod:`fg_env.actions.faults`)."""
        world = self.world
        mark = world.journal.mark() if mark is None else mark
        try:
            with world.journal.held():
                return work(), None
        except FatalRunError:
            raise
        except (RunError, ExprError) as exc:
            world.journal.rollback(mark)
            error = exc if isinstance(exc, RunError) else RunError(str(exc))
            if isinstance(error, InvariantViolation):
                # Already broken before the action (by something no invariant check followed): not the action's doing.
                self.check_invariants("changes made before an agent's action")
            path = error.path or "actions"
            self.diagnosis.faulted(path, str(error).removeprefix(f"{path}: "), action)
            return None, fault_reason(error)

    # -- events --------------------------------------------------------------------------------------------------

    def fire(self, anchor: str, vars: dict[str, Any] | None = None, owner: Entity | None = None) -> None:
        """Fire the events on ``anchor`` (see :meth:`Events.fire`)."""
        self.events.fire(anchor, vars, owner)

    def check_changes(self, path: str) -> None:
        """Fire the `change` events a commit at ``path`` set off (see :meth:`Events.check_changes`)."""
        self.events.check_changes(path)

    def run_scheduled(self) -> None:
        """Apply the scheduled effects and messages due this round (see :meth:`Events.run_scheduled`)."""
        self.events.run_scheduled()

    # -- invariants and the end ------------------------------------------------------------------------------------

    def check_invariants(self, path: str, moment: str = "action") -> None:
        """Check the invariants due at ``moment``: build, action (after a change), round or end. An
        invariant already found to hold in exactly this state — purely: drawing nothing and reading nothing hidden —
        holds again, so it is not evaluated again."""
        if not self.contract.invariants:
            return
        world, held = self.world, self.state.invariant_held
        scope = world.scope()
        for index, invariant in enumerate(self.contract.invariants):
            if moment not in _INVARIANT_MOMENTS[invariant.check]:
                continue
            state = world.state_version()
            if moment == "action" and held.get(index) == state:
                continue
            observed = world.luck.observe()
            try:
                holds = self._touched_hold(invariant) if invariant.check == "action" else None
                if holds is None:
                    holds = truthy(compile_expr(invariant.expr)(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"invariants[{index}]") from None
            if not holds:
                why = _why(invariant.why, scope, f"invariants[{index}].why")
                raise InvariantViolation(f"invariant `{invariant.expr}` no longer holds after {path}"
                                         f"{f' ({why})' if why else ''}", f"invariants[{index}]", why)
            held[index] = state if observed.pure(state, world.state_version()) else None
        if moment in _INVARIANT_MOMENTS["action"]:  # every action invariant was due, and holds
            world.touched = {}

    def _touched_hold(self, invariant: Any) -> bool | None:
        """Whether an `$all` invariant over members' own properties holds for every member created or changed since
        the invariants last held (the rest are as they were then); None when it must be checked whole."""
        world, touched = self.world, self.world.touched
        if touched is None:
            return None
        terms = item_conditions(invariant.expr)
        if not terms or any(word not in self.contract.types for word, _ in terms):
            return None
        for word, condition in terms:
            kinds = world.subtypes_of(word)
            for entity_id in touched:
                entity = world.entities.get(entity_id)
                if entity is not None and entity.alive and entity.entity_type in kinds \
                        and not truthy(condition(world.scope(it=entity))):
                    return False
        return True

    def check_end(self, moment: str = "stage") -> None:
        """Request the end of the run when an end condition holds. ``moment`` is ``stage`` (the round's set
        points: every condition) or ``action`` (something just committed: the conditions with `check: action`)."""
        world = self.world
        if world.end_request is not None or world.round == 0:
            return
        scope = None
        for index, end in enumerate(self.contract.end):
            if moment == "action" and end.check != "action":
                continue
            path = f"end[{index}]"
            scope = scope or world.scope()
            try:
                if not truthy(compile_expr(end.when)(scope)):
                    continue
                winner = _plain(compile_expr(end.winner)(scope)) if end.winner else None
                text = compile_template(end.say, None).render(scope) if end.say else ""
            except ExprError as exc:
                raise RunError(str(exc), path) from None
            world.request_end(end.name or f"end_{index}", winner, text)
            return

    # -- actions -------------------------------------------------------------------------------------------------

    def blocked(self, actor: Entity, name: str, used: dict[str, int], used_round: dict[str, int] | None = None,
                offered: bool = False) -> str | None:
        """Why ``actor`` may not take action ``name`` now, having used each action ``used`` times this turn and
        ``used_round`` times this round (default: as the world counts them); None when it may. ``offered``: whether
        it is asked to offer the action as a tool (see :meth:`ActionBook.blocked`)."""
        if used_round is None:
            used_round = self.world.used_round.get(actor.id, {})
        return self.actions.blocked(actor, name, used, used_round, offered)

    def validate(self, actor: Entity, name: str, args: Any) -> tuple[dict[str, Any], str | None]:
        """``actor``'s arguments ``args`` for ``name`` as its effects read them, or why they are not valid."""
        return self.actions.validate(actor, name, args)

    def apply(self, actor: Entity, name: str, params: dict[str, Any]) -> Outcome:
        """Apply ``name`` with its validated ``params`` (the caller commits it)."""
        return self.actions.apply(actor, name, params)

    def trial(self, actor: Entity, name: str, params: dict[str, Any]) -> str | None:
        """Try ``name`` without keeping anything and without drawing luck: why it would be refused, or None."""
        return self.actions.dry_run(actor, name, params)

    @contextmanager
    def replay_intents(self, actor: Entity, intents: Sequence[tuple[str, dict[str, Any]]]) -> Iterator[None]:
        """The world as ``actor``'s next sealed choice will meet it when it commits: after the ``intents`` it already
        submitted, undone on the way out (two buys cannot spend the same coins). Blocks do not nest."""
        if not intents:
            yield
            return
        with self.actions.trying():
            self.actions.replay(actor, intents)
            yield

    def invalid(self, actor: Entity, stage: StageSpec) -> str | None:
        """Why ``actor``'s turn as played breaks ``stage``'s `valid` rules, or None when it meets them."""
        path = f"stages.{stage.name}.valid"
        scope = self.world.scope(actor=actor)
        with shared_budget(ACTION_BUDGET, path):
            for index, condition in enumerate(stage.valid):
                try:
                    if truthy(compile_expr(condition.expr)(scope)):
                        continue
                    why = compile_template(condition.why, None).render(scope) if condition.why else ""
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}[{index}]") from None
                return str(why).strip().rstrip(".") or "this turn is not allowed"
        return None


def _why(template: str, scope: Scope, path: str) -> str:
    """An invariant's `why`, rendered: the agent whose action broke it is told, so it may read no agent's private
    property (whose action it is, the invariant does not know)."""
    if not template:
        return ""
    try:
        return compile_template(template, None).render(scope.child(viewer=EVERYONE))
    except ExprError as exc:
        raise RunError(str(exc), path) from None
