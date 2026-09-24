"""What the world does on its own: scheduled effects, events and reactions.

One runner fires the events of every anchor at its place in the round (see :class:`~fg_env.contract.EventSpec`):
authored events before generated ones, in declaration order. Every change still goes through the run's atomic blocks
(:meth:`Env._atomic`); events on ``create.<t>`` and ``remove.<t>`` run inside the change that set them off (see
:meth:`EffectRunner.lifecycle`).
"""
from __future__ import annotations

import heapq
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET
from ..actions.faults import world_logic_refused
from ..contract import EventSpec, StageSpec
from ..effects.delivery import run_delivery
from ..effects.runner import each_items, removed_since, select_ops
from ..effects.sync import run_synced
from ..errors import RunError
from ..expr import EVERYONE, ExprError, compile_expr, resolve, shared_budget, truthy
from ..expr.objects import Entity
from ..expr.template import compile_template
from ..world.live import Abort
from .diagnosis import LoopWrites
from .turn import Turn

if TYPE_CHECKING:
    from .env import Env

__all__ = ["Happenings"]

#: The stage hook each stage anchor took the place of: its events draw from that hook's stream.
_HOOK_STREAMS = {"start": "on_enter", "end": "on_exit", "turn": "on_idle"}


def _streams(events: list[EventSpec]) -> list[tuple[str, str]]:
    """Each event's random streams, for its `when` and for its `do`: those of what it was written as before events
    absorbed triggers and stage hooks (a round event counted among round events, a change event among change events), so
    every contract keeps its luck."""
    streams, rounds, changes = [], 0, 0
    for index, event in enumerate(events):
        kind, _, rest = event.on.partition(".")
        if kind == "round":
            streams.append((f"events[{rounds}]", f"events[{rounds}].do"))
            rounds += 1
        elif kind == "change":
            streams.append((f"triggers[{changes}].when", f"triggers[{changes}].do"))
            changes += 1
        elif kind == "stage":
            stage, _, point = rest.rpartition(".")
            hook = f"stages.{stage}.{_HOOK_STREAMS[point]}"
            streams.append((hook, hook))
        else:  # create / remove: they draw with the change they run in
            streams.append((f"events[{index}]", f"events[{index}].do"))
    return streams


def _loop(event: EventSpec) -> Mapping[str, Any] | None:
    """The `each` loop that is a round event's whole `do`, whose items run one by one (see :meth:`Happenings._each`)."""
    do = event.do
    if len(do) == 1 and isinstance(do[0], dict) and select_ops(do[0]) == ["each"] and event.on.startswith("round."):
        return do[0]
    return None


class Happenings:
    """Scheduled effects, events and reactions of one run."""

    #: How deep `change` events may set off further ones (deeper is an error), and reactions further reactions (deeper
    #: reactions wait for the agent's next turn).
    CHANGE_DEPTH = 8
    REACTION_DEPTH = 4

    def __init__(self, env: Env):
        self.env = env
        self._streams = _streams(env.contract.events)
        self._change_depth = 0
        self._reaction_depth = 0

    def run_scheduled(self) -> None:
        env, world = self.env, self.env.world
        while world.scheduled and world.scheduled[0][0] <= world.round:
            _, _, item = heapq.heappop(world.scheduled)
            if "delivery" in item:
                run_delivery(env, item)
            else:
                env._atomic(item["effects"], world.thaw(item["vars"], version=item.get("capture_version", 0)),
                            item["path"])

    def fire(self, anchor: str, vars: dict[str, Any] | None = None, owner: Entity | None = None) -> None:
        """Fire the events on ``anchor`` whose `when` holds, in order, with ``vars`` (a turn's $actor, $acted,
        $timed_out) and drawing as ``owner``. It stops once the run ends, or once ``owner`` is gone."""
        env, world = self.env, self.env.world
        for index, event in env.contract.events_on(anchor):
            if event.once and index in env.state.fired_once:
                continue
            path = f"events[{index}]"
            when, do = self._streams[index]
            with world.drawing_for(when, owner):
                if event.when is not None and not self._holds(event.when, vars or {}, f"{path}.when"):
                    continue
                if event.once:
                    env.state.fired_once.add(index)
                loop = _loop(event)
                if loop is not None:
                    self._each(loop, path, when, do)
                else:
                    env._atomic(event.do, dict(vars or {}), f"{path}.do", owner=owner, luck=do)
            self._say(index, event)
            if env._ended() or (owner is not None and not owner.alive):
                return

    def _holds(self, condition: str, vars: dict[str, Any], path: str) -> bool:
        try:
            return truthy(compile_expr(condition)(self.env.world.scope(**vars)))
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    def _each(self, loop: Mapping[str, Any], path: str, when: str, do: str) -> None:
        """A round event whose `do` is one `each` runs item by item: each item is its own step with luck of its own, so
        one item's draws never shift another's, and invariants are checked once every item has run."""
        env, world = self.env, self.env.world
        name, where = loop.get("as") or "it", loop.get("where")
        body = f"{path}.do[0].do"
        try:
            listed = loop["each"]
            with world.drawing_at(do):  # what the loop goes over is drawn as its `do` would draw it
                items = world.entities_of(listed) if isinstance(listed, str) and listed in env.contract.types else \
                    each_items(resolve(listed, world.scope()), world, f"{path}.do[0].each")
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
                        with world.drawing_for(f"{when}.where", item):
                            if not truthy(compile_expr(where)(world.scope(**inner))):
                                continue
                    if watch is not None:
                        watch.item, watch.position = item, position
                    env._atomic(loop.get("do") or [], inner, body, check=False, owner=item, luck=do)
            finally:
                if watch is not None:
                    world.watched_writes = None
            env._check_invariants(body)
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    def _synced(self, loop: Mapping[str, Any], items: list[Any], name: str, body: str, when: str, do: str) -> None:
        """:meth:`_each` of a `sync` loop: every item reads the world as it was, and all their writes land together, as
        one change."""
        env, world = self.env, self.env.world
        where = loop.get("where")

        def run_item(position: int, item: Any) -> bool:
            inner = {name: item, "i": position}
            if where is not None:
                with world.drawing_for(f"{when}.where", item):
                    if not truthy(compile_expr(where)(world.scope(**inner))):
                        return False
            with shared_budget(ACTION_BUDGET, body), world.drawing_for(do, item):
                env.effects.run(loop.get("do") or [], dict(inner), body)
            return True

        with env._lock:
            mark = world.journal.mark()
            try:
                ran = run_synced(world, items, run_item, body)
            except Abort as refusal:
                world.journal.rollback(mark)
                raise RunError(world_logic_refused(refusal.reason), body) from None
            except BaseException:
                world.journal.rollback(mark)
                raise
            if ran:
                env._after_commit(body)
                self.react(env._stage_spec())

    def _say(self, index: int, event: EventSpec) -> None:
        if not event.say:
            return
        world = self.env.world
        try:
            text = compile_template(event.say, None).render(world.scope(viewer=EVERYONE))
        except ExprError as exc:
            raise RunError(str(exc), f"events[{index}].say") from None
        if text.strip():
            world.emit("news", text, data={"event": event.name or index})
        world.journal.clear()

    def check_changes(self, path: str) -> None:
        """Fire every `change` event whose `when` has just become true (after a commit at ``path``)."""
        env = self.env
        events = env.contract.events_on("change")
        if not events or env._ended():
            return
        if self._change_depth >= self.CHANGE_DEPTH:
            raise RunError(f"events on 'change' set each other off more than {self.CHANGE_DEPTH} levels deep (a "
                           "loop?)", path)
        world = env.world
        self._change_depth += 1
        try:
            for index, event in events:
                if event.once and index in env.state.fired_once:
                    continue
                when, do = self._streams[index]
                with world.drawing_at(when):
                    holds = self._holds(event.when or "true", {}, f"events[{index}].when")
                was = env.state.armed.get(index, False)
                env.state.armed[index] = holds
                if not holds or was:
                    continue
                if event.once:
                    env.state.fired_once.add(index)
                env._check_invariants(path)  # a change event never acts on a broken world (an `each` item checks late)
                env._atomic(event.do, {}, f"events[{index}].do", luck=do)
                self._say(index, event)
                if env._ended():
                    return
        finally:
            self._change_depth -= 1

    def react(self, stage: StageSpec | None) -> None:
        """Give every agent asked to react (`wake` with `now`) a turn right away, in the current stage — offered the
        actions the wake names, else the stage's: once the action that woke them has committed, so a reaction answers it
        and cannot undo it. While an agent's action is still committing (and could yet be undone), they wait for it to
        finish. Reactions to reactions nested deeper than :attr:`REACTION_DEPTH` become ordinary wakes: agents that keep
        answering each other never fail the run."""
        env, world = self.env, self.env.world
        if world.journal.holding:
            return
        while world.reactions and not env._ended():
            entity_id, why, actions = world.reactions.pop(0)
            actor = world.entities.get(entity_id)
            if actor is None or not actor.alive or not env.contract.is_agent(actor.entity_type):
                continue
            if self._reaction_depth >= self.REACTION_DEPTH:  # agents answering each other: the rest wait a turn
                world.request_wake(entity_id, why)
                continue
            spec = stage or next(iter(env.contract.stage_list()))
            if actions is not None:  # the answers the wake names, not every action of the stage
                spec = spec.model_copy(update={"actions": list(actions)})
            self._reaction_depth += 1
            try:
                turn = Turn(env, actor, spec, why, staged=False, kind="reaction")
                turn.stats.reactions = 1
                env.driver.drive([turn])
                env._timed_out(turn)
            finally:
                self._reaction_depth -= 1
            memory = env.state.memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1
