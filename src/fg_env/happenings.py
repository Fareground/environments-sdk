"""What the world does on its own: scheduled effects, events, triggers and reactions.

Every change still goes through the run's atomic blocks (:meth:`Env._atomic`).
"""
from __future__ import annotations

import heapq
from typing import TYPE_CHECKING, Any, List, Optional

from .delivery import run_delivery
from .contract import StageSpec
from .build import whole_setting
from .errors import RunError
from .expr import ExprError, compile_expr, truthy
from .sync_events import run_sync
from .template import compile_template
from .turn import Turn

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["Happenings"]


class Happenings:
    """Scheduled effects, events, triggers and reactions of one run."""

    #: How deep triggers may set off further triggers, and reactions further reactions.
    TRIGGER_DEPTH = 8
    REACTION_DEPTH = 4

    def __init__(self, env: "Env"):
        self.env = env
        self._trigger_depth = 0
        self._reaction_depth = 0

    def run_scheduled(self) -> None:
        env, world = self.env, self.env.world
        while world.scheduled and world.scheduled[0][0] <= world.now():
            _, _, item = heapq.heappop(world.scheduled)
            if "delivery" in item:
                run_delivery(env, item)
            else:
                env._atomic(item["effects"], world.thaw(item["vars"], version=item.get("capture_version", 0)),
                            item["path"])

    def run_events(self, phase: str) -> None:
        env, world = self.env, self.env.world
        for index, event in enumerate(env.contract.events):
            if event.phase != phase:
                continue
            path = f"events[{index}]"
            if event.arms is not None and env.arm not in event.arms:
                continue
            if event.once and index in env._fired_once:
                continue
            with world.drawing_at(path):  # its own luck: no other event's draws, nor any agent's, shift it
                self._fire(index, event, path)
            if env._ended():
                return

    def _fire(self, index: int, event: Any, path: str) -> None:
        env, world = self.env, self.env.world
        if not self._due(event, path):
            return
        if event.once:
            env._fired_once.add(index)
        if event.each is not None:
            item_name = event.as_ or "it"
            try:
                items = world.entities_of(event.each) if event.each in env.contract.types else \
                    compile_expr(event.each)(world.scope())
                items = self._ordered(event, list(items or []), item_name, path)
                if event.sync:
                    run_sync(env, event, items, item_name, path)
                    items = []
                for position, item in enumerate(items):
                    inner = {item_name: item, "i": position}
                    if event.where is not None and not truthy(compile_expr(event.where)(world.scope(**inner))):
                        continue
                    env._atomic(event.do, inner, f"{path}.do", check=False)
                env._check_invariants(f"{path}.do")
            except ExprError as exc:
                raise RunError(str(exc), path) from None
        else:
            env._atomic(event.do, {}, f"{path}.do")
        if event.say:
            try:
                text = compile_template(event.say, None).render(world.scope())
            except ExprError as exc:
                raise RunError(str(exc), f"{path}.say") from None
            if text.strip():
                world.emit("news", text, data={"event": event.name or index})
            world.journal.clear()

    def _ordered(self, event: Any, items: List[Any], name: str, path: str) -> List[Any]:
        """An `each` event's items in its `order`: shuffled from the run's seed, or by a key (lowest first)."""
        world = self.env.world
        if event.order is None:
            return items
        if event.order == "random":
            world.rng.shuffle(items)
            return items
        key = compile_expr(event.order)
        keyed = [(key(world.scope(**{name: item, "i": position})), position, item) for position, item in enumerate(items)]
        try:
            keyed.sort(key=lambda entry: (entry[0], entry[1]))
        except TypeError:
            raise RunError("`order` must give comparable values (numbers or text)", f"{path}.order") from None
        return [item for _, _, item in keyed]

    def _due(self, event: Any, path: str) -> bool:
        world = self.env.world
        scope = world.scope()
        try:
            if event.at is not None:
                at = compile_expr(event.at)(scope) if isinstance(event.at, str) else event.at
                rounds = at if isinstance(at, list) else [at]
                if world.round not in rounds:
                    return False
            every = whole_setting(world, event.every, f"{path}.every")
            if every is not None and (world.round - 1) % every != 0:
                return False
            if event.when is not None and not truthy(compile_expr(event.when)(scope)):
                return False
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        return True

    def check_triggers(self, path: str) -> None:
        env = self.env
        if not env.contract.triggers or env._ended():
            return
        if self._trigger_depth >= self.TRIGGER_DEPTH:
            raise RunError(f"triggers set each other off more than {self.TRIGGER_DEPTH} levels deep (a loop?)", path)
        world = env.world
        self._trigger_depth += 1
        try:
            for index, trigger in enumerate(env.contract.triggers):
                if trigger.arms is not None and env.arm not in trigger.arms:
                    continue
                if trigger.once and index in env._triggers_fired:
                    continue
                where = f"triggers[{index}]"
                try:
                    with world.drawing_at(f"{where}.when"):
                        holds = truthy(compile_expr(trigger.when)(world.scope()))
                except ExprError as exc:
                    raise RunError(str(exc), f"{where}.when") from None
                was = env._trigger_armed.get(index, False)
                env._trigger_armed[index] = holds
                if not holds or was:
                    continue
                if trigger.once:
                    env._triggers_fired.add(index)
                env._atomic(trigger.do, {}, f"{where}.do")
                if trigger.say:
                    try:
                        text = compile_template(trigger.say, None).render(world.scope())
                    except ExprError as exc:
                        raise RunError(str(exc), f"{where}.say") from None
                    if text.strip():
                        world.emit("news", text, data={"trigger": trigger.name or index})
                    world.journal.clear()
                if env._ended():
                    return
        finally:
            self._trigger_depth -= 1

    def react(self, stage: Optional[StageSpec]) -> None:
        """Give every agent asked to react (`wake` with `now`) a turn right away, in the current stage: once the
        action that woke them has committed, so a reaction answers it and cannot undo it. While an agent's action is
        still committing (and could yet be undone), they wait for it to finish."""
        env, world = self.env, self.env.world
        if world.journal.holding:
            return
        while world.reactions and not env._ended():
            entity_id, why = world.reactions.pop(0)
            actor = world.entities.get(entity_id)
            if actor is None or not actor.alive or not env.contract.is_agent(actor.entity_type):
                continue
            if self._reaction_depth >= self.REACTION_DEPTH:
                raise RunError(f"reactions set each other off more than {self.REACTION_DEPTH} levels deep", "wake.now")
            spec = stage or next(iter(env.contract.stage_list()))
            self._reaction_depth += 1
            try:
                turn = Turn(env, actor, spec, why, staged=False, kind="reaction")
                turn.stats.reactions = 1
                env.driver.drive([turn])
                env._timed_out(turn)
            finally:
                self._reaction_depth -= 1
            memory = env._memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1
