"""What the world does on its own: scheduled effects and messages, and the events on each anchor.

One runner fires the events of an anchor when the schedule reaches it (see :class:`~fg_env.contract.EventSpec`):
authored events before generated ones, in declaration order. Every change goes through the rules' atomic blocks
(:meth:`Rules.run_block`); events on ``create.<t>`` and ``remove.<t>`` run inside the change that set them off (see
:meth:`EffectRunner.lifecycle`).
"""
from __future__ import annotations

import heapq
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET
from ..actions.faults import world_logic_refused
from ..contract import EventSpec
from ..effects.captures import thaw
from ..effects.delivery import deliver
from ..effects.runner import each_items, removed_since, select_ops
from ..effects.sync import run_synced
from ..errors import RunError
from ..expr import EVERYONE, ExprError, compile_expr, resolve, shared_budget, truthy
from ..expr.objects import Entity
from ..world.abort import Abort
from ..world.randomness import event_streams
from .diagnosis import LoopWrites
from .facts import CommitRefused

if TYPE_CHECKING:
    from .rules import Rules

__all__ = ["Events"]


def _loop(event: EventSpec) -> Mapping[str, Any] | None:
    """The `each` loop that is a round event's whole `do`, whose items run one by one (see :meth:`Events._each`)."""
    do = event.do
    if len(do) == 1 and isinstance(do[0], dict) and select_ops(do[0]) == ["each"] and event.on.startswith("round."):
        return do[0]
    return None


class Events:
    """Scheduled effects and events of one run, applied through its ``rules``."""

    #: How deep `change` events may set off further ones (deeper is an error).
    CHANGE_DEPTH = 8

    def __init__(self, rules: Rules):
        self.rules = rules
        self._streams = event_streams(event.on for event in rules.contract.events)
        self._change_depth = 0

    def run_scheduled(self) -> None:
        """Apply the scheduled effects and messages due this round, each as its own change."""
        rules, world = self.rules, self.rules.world
        while world.scheduled and world.scheduled[0][0] <= world.round:
            _, _, item = heapq.heappop(world.scheduled)
            if "delivery" in item:
                self._deliver(item)
            elif "by" in item:
                self._continue(item)
            else:
                rules.run_block(item["effects"], thaw(item["vars"], world, version=item.get("capture_version", 0)),
                                item["path"])

    def _continue(self, item: Mapping[str, Any]) -> None:
        """Effects an agent's action scheduled with `after`: still that action's. What refuses the action refuses them
        (a `fail`, a transfer or write that does not fit, a rule that fails, an invariant they break, its agent having
        left the run): the block alone is undone, the agent is told why in words that reveal nothing hidden from it,
        the run's diagnostics count it against the action, and the run goes on — as when a sealed choice is refused as
        it commits."""
        rules, world = self.rules, self.rules.world
        by, name = item["by"], item["action"]
        actor = world.entities.get(by)
        vars = thaw(item["vars"], world, version=item.get("capture_version", 0))

        def work() -> None:
            rules.run_block(item["effects"], vars, item["path"], refusable=True)

        with rules.gate:
            if actor is not None and not actor.alive:  # its agent has left: nothing of its action happens any more,
                rules.facts.emit(CommitRefused(name, "you are no longer active"))  # as with a sealed choice
                return
            try:
                if actor is None:
                    _, why = rules.guarded(work, action=name)
                else:
                    with world.luck.acting_as(actor, name):
                        _, why = rules.guarded(work, action=name)
                faulted = why is not None
            except Abort as refusal:
                why, faulted = refusal.reason.strip().rstrip("."), False
            if why is None:
                return
            if actor is not None and actor.alive:
                world.emit("outcome", f"What your {name.replace('_', ' ')} set for later did not happen: {why}.",
                           actor=by, to=(by,), data={"action": name, "ok": False})
            rules.facts.emit(CommitRefused(name, why, faulted=faulted))
            world.commit()

    def _deliver(self, item: Mapping[str, Any]) -> None:
        """Deliver one scheduled message as its own atomic change."""
        rules, world = self.rules, self.rules.world
        with rules.gate:
            mark = world.mark()
            try:
                deliver(world, item["delivery"], item["path"])
            except BaseException:
                world.rollback(mark)
                raise
            rules.commit(item["path"])
            rules.react(rules.stage_spec())

    def fire(self, anchor: str, vars: dict[str, Any] | None = None, owner: Entity | None = None) -> None:
        """Fire the events on ``anchor`` whose `when` holds, in order, with ``vars`` (a turn's $actor, $acted,
        $timed_out) and drawing as ``owner``. It stops once the run ends, or once ``owner`` is gone."""
        rules, world = self.rules, self.rules.world
        for index, event in rules.contract.events_on(anchor):
            if event.once and index in world.fired_once:
                continue
            path = f"events[{index}]"
            when, do = self._streams[index]
            with world.luck.at(when, owner):
                if event.when is not None and not self._holds(event.when, vars or {}, f"{path}.when"):
                    continue
                if event.once:
                    world.mark_fired(index)
                loop = _loop(event)
                if loop is not None:
                    self._each(loop, path, when, do)
                else:
                    rules.run_block(event.do, dict(vars or {}), f"{path}.do", owner=owner, luck=do)
            self._say(index, event)
            world.commit()  # the event has run: its firing commits with it
            if rules.ended() or (owner is not None and not owner.alive):
                return

    def _holds(self, condition: str, vars: dict[str, Any], path: str) -> bool:
        try:
            return truthy(compile_expr(condition)(self.rules.evaluation.scope(**vars)))
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    def _each(self, loop: Mapping[str, Any], path: str, when: str, do: str) -> None:
        """A round event whose `do` is one `each` runs item by item: each item is its own step with luck of its own, so
        one item's draws never shift another's, and invariants are checked once every item has run."""
        rules, world = self.rules, self.rules.world
        name, where = loop.get("as") or "it", loop.get("where")
        body = f"{path}.do[0].do"
        try:
            listed = loop["each"]
            with world.luck.at(do):  # what the loop goes over is drawn as its `do` would draw it
                items = world.entities_of(listed) if isinstance(listed, str) and listed in rules.contract.types else \
                    each_items(resolve(listed, self.rules.evaluation.scope()), world, f"{path}.do[0].each")
            if loop.get("sync"):
                self._synced(loop, items, name, body, when, do)
                return
            removed = removed_since(items)
            watch = LoopWrites.start(world, dict(loop), f"{path}.do[0]")
            try:
                for position, item in enumerate(items):
                    if removed(position):
                        continue
                    inner = {name: item, "i": position}
                    if where is not None:
                        with world.luck.at(f"{when}.where", item):
                            if not truthy(compile_expr(where)(self.rules.evaluation.scope(**inner))):
                                continue
                    if watch is not None:
                        watch.item, watch.position = item, position
                    rules.run_block(loop.get("do") or [], inner, body, check=False, owner=item, luck=do)
            finally:
                if watch is not None:
                    world.watched_writes = None
            rules.check_invariants(body)
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    def _synced(self, loop: Mapping[str, Any], items: list[Any], name: str, body: str, when: str, do: str) -> None:
        """:meth:`_each` of a `sync` loop: every item reads the world as it was, and all their writes land together, as
        one change."""
        rules, world = self.rules, self.rules.world
        where = loop.get("where")

        def run_item(position: int, item: Any) -> bool:
            inner = {name: item, "i": position}
            if where is not None:
                with world.luck.at(f"{when}.where", item):
                    if not truthy(compile_expr(where)(self.rules.evaluation.scope(**inner))):
                        return False
            with shared_budget(ACTION_BUDGET, body), world.luck.at(do, item):
                rules.effects.run(loop.get("do") or [], dict(inner), body)
            return True

        with rules.gate:
            mark = world.mark()
            try:
                ran = run_synced(world, items, run_item, body)
            except Abort as refusal:
                world.rollback(mark)
                raise RunError(world_logic_refused(refusal.reason), body) from None
            except BaseException:
                world.rollback(mark)
                raise
            if ran:
                rules.commit(body)
                rules.react(rules.stage_spec())

    def _say(self, index: int, event: EventSpec) -> None:
        if not event.say:
            return
        world = self.rules.world
        text = self.rules.information.render(event.say, {}, viewer=EVERYONE, path=f"events[{index}].say")
        if text.strip():
            world.emit("news", text, data={"event": event.name or index})
        world.commit()

    def check_changes(self, path: str) -> None:
        """Fire every `change` event whose `when` has just become true (after a commit at ``path``)."""
        rules = self.rules
        events = rules.contract.events_on("change")
        if not events or rules.ended():
            return
        if self._change_depth >= self.CHANGE_DEPTH:
            raise RunError(f"events on 'change' set each other off more than {self.CHANGE_DEPTH} levels deep (a "
                           "loop?)", path)
        self._change_depth += 1
        try:
            self._fire_changes(events, path)
        finally:
            self._change_depth -= 1
        rules.world.commit()  # the `when`s it found changed commit with the change that moved them

    def _fire_changes(self, events: list[tuple[int, EventSpec]], path: str) -> None:
        rules, world = self.rules, self.rules.world
        for index, event in events:
            if event.once and index in world.fired_once:
                continue
            when, do = self._streams[index]
            with world.luck.at(when):
                holds = self._holds(event.when or "true", {}, f"events[{index}].when")
            was = world.armed.get(index, False)
            world.set_armed(index, holds)
            if not holds or was:
                continue
            if event.once:
                world.mark_fired(index)
            rules.check_invariants(path)  # a change event never acts on a broken world (an `each` item checks late)
            rules.run_block(event.do, {}, f"events[{index}].do", luck=do)
            self._say(index, event)
            if rules.ended():
                return
