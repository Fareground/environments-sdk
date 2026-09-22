"""What a run notices about its own rules while it plays, read by :mod:`fg_env.sdk.diagnostics`.

Only counts: how often each action was refused and why, rules that failed while an agent's action applied, whether a
refused tool had any choice that could have worked, which stages were reached, ran and woke agents, whether each agent
type ever had an action it could take, which properties were written, and sealed choices that replaced each other's
writes. It is saved in snapshots, so
a resumed run reports exactly what a straight run does.
"""
from __future__ import annotations

import itertools
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Tuple

from ..entity import Entity
from .actions import stage_actions

if TYPE_CHECKING:
    from .session import ToolResult
    from .turn import Turn

__all__ = ["Diagnosis", "SealedWrites", "PROBE_LIMIT"]

#: Numbers and quoted participant text vary between refusals that share one cause.
_VARIES = re.compile(r"«[^»]*»|-?\d+(?:\.\d+)?")
#: Tool results that say nothing about the rules: the turn was over or out of time.
_NOT_ABOUT_RULES = ("ended", "timeout")
#: The most argument combinations tried when checking whether a refused tool had any choice that works.
PROBE_LIMIT = 64
#: A whole-number parameter with at most this many values is tried at every value; a wider one at its edges and middle.
_EVERY_VALUE = 32
_OMITTED = object()
_ASSIGNMENT = re.compile(r"(?<![=!<>])=(?!=)")


def _tally(reasons: Dict[str, List[Any]], text: str) -> None:
    """Count ``text`` under its cause (numbers and quotes blanked), keeping the first wording seen."""
    entry = reasons.setdefault(_VARIES.sub("#", text), [0, text])
    entry[0] += 1


class Diagnosis:
    """The counts one run keeps for its diagnostics."""

    def __init__(self, written: Set[str]):
        #: action → {calls, refused, reasons}, plus {unusable, stuck}: refusals when no choice the tool offered could
        #: have worked, and their wordings.
        self.actions: Dict[str, Dict[str, Any]] = {}
        #: stage → [times reached, times run, agents woken]
        self.stages: Dict[str, List[int]] = {}
        #: agent type → {wakes, able, rounds, last_round, reasons}
        self.agents: Dict[str, Dict[str, Any]] = {}
        #: stage → [overwrites, first example]
        self.overwrites: Dict[str, List[Any]] = {}
        #: `each` effect path → [overwrites, first example]
        self.loop_overwrites: Dict[str, List[Any]] = {}
        #: Path of a rule that failed (or invariant that broke) while an agent's action applied → [times, first error]
        self.faults: Dict[str, List[Any]] = {}
        #: Names of properties written since the world was built (shared with the world, which adds to it).
        self.written = written
        #: The turn number and actions already probed in it (not saved: snapshots fall between turns).
        self._probed: Tuple[int, Set[str]] = (0, set())

    # -- actions ---------------------------------------------------------------------

    def called(self, turn: "Turn", name: str, args: Any, result: "ToolResult") -> None:
        """A tool call finished: count it against its action; when it was refused, check whether any choice the
        tool offered could have worked."""
        env = turn.env
        if (result.data or {}).get("error") in _NOT_ABOUT_RULES:
            return
        if name not in env.contract.actions and name in env.actions.groups:
            name = env.actions.route(name, args, ())[0]
        if name not in env.contract.actions:
            return
        entry = self._action(name)
        entry["calls"] += 1
        if result.ok:
            return
        entry["refused"] += 1
        _tally(entry["reasons"], result.text)
        if self._first_probe(turn, name) and usable(turn, name) is False:
            entry["unusable"] += 1
            _tally(entry["stuck"], result.text)

    def refused_at_commit(self, name: str, text: str) -> None:
        """A sealed choice accepted when submitted did not happen when the choices committed."""
        entry = self._action(name)
        entry["refused"] += 1
        _tally(entry["reasons"], text)

    def faulted(self, path: str, error: str) -> None:
        """A rule at ``path`` failed, or the invariant at ``path`` broke, while an agent's action applied (which was
        refused and undone)."""
        entry = self.faults.setdefault(path, [0, error])
        entry[0] += 1

    def _action(self, name: str) -> Dict[str, Any]:
        return self.actions.setdefault(name, {"calls": 0, "refused": 0, "reasons": {}, "unusable": 0, "stuck": {}})

    def _first_probe(self, turn: "Turn", name: str) -> bool:
        if self._probed[0] != turn.number:
            self._probed = (turn.number, set())
        if name in self._probed[1]:
            return False
        self._probed[1].add(name)
        return True

    # -- stages and agents -----------------------------------------------------------

    def stage(self, name: str, reached: int = 0, ran: int = 0, woke: int = 0) -> None:
        counts = self.stages.setdefault(name, [0, 0, 0])
        counts[0] += reached
        counts[1] += ran
        counts[2] += woke

    def offered(self, turn: "Turn", has_action: bool) -> None:
        """A fresh turn's tools were read: note whether the agent had any action it could take, and if not why."""
        if turn.actions_left < turn.stage.max_actions or turn.intents:
            return
        entry = self.agents.setdefault(turn.actor.entity_type,
                                       {"wakes": 0, "able": 0, "rounds": 0, "last_round": 0, "reasons": {}})
        entry["wakes"] += 1
        if entry["last_round"] != turn.round:
            entry["rounds"] += 1
            entry["last_round"] = turn.round
        if has_action:
            entry["able"] += 1
            return
        env, actor = turn.env, turn.actor
        used_round = env._used_round.get(actor.id, {})
        for name in stage_actions(env.contract, turn.stage, actor.entity_type):
            why = env.actions.blocked(actor, name, turn.used, used_round)
            if why:
                _tally(entry["reasons"], f"{name}: {why}")
                return
        _tally(entry["reasons"], f"none of the actions of stage {turn.stage.name} was offered")

    def overwrote(self, stage: str, example: str, loop: bool = False) -> None:
        entry = (self.loop_overwrites if loop else self.overwrites).setdefault(stage, [0, example])
        entry[0] += 1

    # -- saving ----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {"actions": _copy(self.actions), "stages": _copy(self.stages), "agents": _copy(self.agents),
                "overwrites": _copy(self.overwrites), "loop_overwrites": _copy(self.loop_overwrites),
                "faults": _copy(self.faults), "written": sorted(self.written)}

    def load(self, data: Optional[Dict[str, Any]]) -> None:
        """Take the counts of :meth:`to_dict` (the written names in place: the world holds the same set)."""
        data = data or {}
        self.actions, self.stages = _copy(data.get("actions", {})), _copy(data.get("stages", {}))
        self.agents, self.overwrites = _copy(data.get("agents", {})), _copy(data.get("overwrites", {}))
        self.loop_overwrites = _copy(data.get("loop_overwrites", {}))
        self.faults = _copy(data.get("faults", {}))
        self.written.clear()
        self.written.update(data.get("written", []))


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def usable(turn: "Turn", name: str) -> Optional[bool]:
    """Whether any choice the action's tool offers right now would succeed: tried on a copy of the change that is
    rolled back. True as soon as one works; False only when every parameter could be tried and none worked; None
    when that cannot be told (the action is not offered, a parameter is free text or unbounded, or there are too
    many combinations)."""
    env, actor = turn.env, turn.actor
    if turn.actions_left <= 0 or name not in stage_actions(env.contract, turn.stage, actor.entity_type):
        return None
    if env.actions.blocked(actor, name, turn.used, env._used_round.get(actor.id, {})) is not None:
        return None
    found = _axes(env.actions.tool(actor, name, turn.staged).input_schema)
    if found is None:
        return None
    axes, complete = found
    keys = list(axes)
    for tried, values in enumerate(itertools.product(*axes.values())):
        if tried >= PROBE_LIMIT:
            return None
        args = {key: value for key, value in zip(keys, values) if value is not _OMITTED}
        params, problem = env.actions.validate(actor, name, args)
        if problem is None and env.actions.refusal(actor, name, params) is None:
            return True
    return False if complete else None


def _axes(schema: Dict[str, Any]) -> Optional[Tuple[Dict[str, List[Any]], bool]]:
    """The values to try for each parameter, and whether they cover every parameter. None when a required parameter
    cannot be listed (nothing can be tried without it); an optional one that cannot be listed is left out, so trying
    the rest proves only that something works, never that nothing does."""
    required = set(schema.get("required") or [])
    axes: Dict[str, List[Any]] = {}
    complete = True
    for key, prop in (schema.get("properties") or {}).items():
        values = _values(prop)
        if values is None:
            if key in required:
                return None
            complete = False
            values = [_OMITTED]
        axes[key] = values
    return axes, complete


def _values(prop: Dict[str, Any]) -> Optional[List[Any]]:
    if "enum" in prop:
        return list(prop["enum"])
    kind, low, high = prop.get("type"), prop.get("minimum"), prop.get("maximum")
    if kind == "boolean":
        return [False, True]
    if kind not in ("integer", "number") or low is None or high is None or "multipleOf" in prop:
        return None
    if kind == "number":
        return sorted({low, (low + high) / 2, high})
    low, high = int(low), int(high)
    if high - low < _EVERY_VALUE:
        return list(range(low, high + 1))
    return sorted({low, low + 1, (low + high) // 2, high - 1, high})


class SealedWrites:
    """While a simultaneous stage commits its choices: who last assigned each property with `=`, so a choice that
    replaces another agent's different value — without reading it — is noticed."""

    def __init__(self, stage: str, diagnosis: Diagnosis):
        self.stage = stage
        self.diagnosis = diagnosis
        self.writer = ""
        self.action = ""
        self._last: Dict[Any, Any] = {}

    def assigned(self, owner: Any, prop: str, rest: Sequence[Any], value: Any, source: str) -> None:
        """``owner.prop`` (at element path ``rest``) was assigned ``value`` by the current choice."""
        key = (owner.id if isinstance(owner, Entity) else "$world", prop, repr(list(rest)))
        before = self._last.get(key)
        self._last[key] = (self.writer, value)
        sides = _ASSIGNMENT.split(source, 1)
        builds_on_before = len(sides) == 2 and sides[0].strip() in sides[1]
        if before is None or before[0] == self.writer or _same(before[1], value) or builds_on_before:
            return  # the first write, the same agent again, the same value, or a change built on the value before
        shown = f"{owner.name or owner.id}.{prop}" if isinstance(owner, Entity) else f"$world.{prop}"
        self.diagnosis.overwrote(self.stage, f"`{source}` in actions.{self.action} set {shown}, replacing the value "
                                             f"{before[0]}'s choice had set")


class LoopWrites:
    """While an `each` loop runs (outside sealed commits): plain `=` writes to one target — not the loop's own item —
    from different items, when the loop reads that target nowhere else. Every item but the last is then lost."""

    def __init__(self, world: Any, path: str, body: str):
        self.world, self.path, self.body = world, path, body
        self.item: Any = None
        self.position = 0
        self._last: Dict[Any, Any] = {}

    @classmethod
    def start(cls, world: Any, effect: Dict[str, Any], path: str) -> Optional["LoopWrites"]:
        """Watch a loop's writes, unless the run keeps no diagnosis or writes are already watched."""
        if world.diagnosis is None or world.watched_writes is not None:
            return None
        watch = world.watched_writes = cls(world, path, json.dumps(effect, default=str))
        return watch

    def assigned(self, owner: Any, prop: str, rest: Sequence[Any], value: Any, source: str) -> None:
        if owner is self.item:
            return
        target = _ASSIGNMENT.split(source, 1)[0].strip()
        if self.body.count(target) > 1:
            return  # the loop reads the target too: a running best, a guard, a change built on it
        key = (owner.id if isinstance(owner, Entity) else "$world", prop, repr(list(rest)))
        before = self._last.get(key)
        self._last[key] = (self.position, value)
        if before is None or before[0] == self.position or _same(before[1], value):
            return
        self.world.diagnosis.overwrote(self.path, f"`{source}` ran for several items with different values, so only the "
                                                  "last item's value is kept", loop=True)


def _same(a: Any, b: Any) -> bool:
    try:
        return bool(a == b)
    except Exception:  # values that cannot be compared are treated as different
        return False
