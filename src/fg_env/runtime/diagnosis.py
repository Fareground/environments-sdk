"""What a run notices about its own rules while it plays, read by :mod:`fg_env.runtime.diagnostics`: a fold over the
run's facts (:mod:`fg_env.runtime.facts`).

Only counts: how often each action was refused and why, rules that failed while an agent's action applied, whether a
refused tool had any choice that could have worked, which stages were reached, ran and woke agents, whether each agent
type ever had an action it could take, how often each coded policy rule acted and was refused, which properties were
written, and sealed choices that replaced each other's writes. It is saved in snapshots, so
a resumed run reports exactly what a straight run does.
"""
from __future__ import annotations

import itertools
import json
import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from ..actions.book import stage_actions
from ..expr.objects import Entity
from .facts import Answered, CommitRefused, Committed, Fact, Faulted, Offered, Overwrote, PolicyRule, StageVisit

if TYPE_CHECKING:
    from .facts import Facts
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


def _tally(reasons: dict[str, list[Any]], text: str) -> None:
    """Count ``text`` under its cause (numbers and quotes blanked), keeping the first wording in sorted order: agents
    in a sealed stage are refused concurrently, so the order they were refused in is not part of the run."""
    entry = reasons.setdefault(_VARIES.sub("#", text), [0, text])
    entry[0] += 1
    entry[1] = min(entry[1], text)


class Diagnosis:
    """The counts one run keeps for its diagnostics: a fold over its facts (:meth:`fold`)."""

    def __init__(self, written: set[str]):
        #: action → {calls, refused, reasons}, plus {unusable, stuck}: refusals when no choice the tool offered could
        #: have worked, and their wordings; {applied, faulted}: times it took effect, and times a rule failed or an
        #: invariant broke as it applied; {chosen, chosen_refused}: calls a model or a coded policy chose (not blind
        #: random play), and how many of those were refused.
        self.actions: dict[str, dict[str, Any]] = {}
        #: stage → [times reached, times run, agents woken, times it ran every pass without its `until` holding]
        self.stages: dict[str, list[int]] = {}
        #: agent type → {wakes, able, rounds, last_round, reasons}
        self.agents: dict[str, dict[str, Any]] = {}
        #: stage → [overwrites, first example]
        self.overwrites: dict[str, list[Any]] = {}
        #: `each` effect path → [overwrites, first example]
        self.loop_overwrites: dict[str, list[Any]] = {}
        #: Path of a rule that failed (or invariant that broke) while an agent's action applied → [times, first error]
        self.faults: dict[str, list[Any]] = {}
        #: Policy rule path → [times it acted, times its call was refused, the last refusal]
        self.policy_rules: dict[str, list[Any]] = {}
        #: Agents some stage's `who` decided by luck (it drew), so never being woken may be the luck of the draw.
        self.chance_woken: set[str] = set()
        #: Names of properties written since the world was built (shared with the world, which adds to it).
        self.written = written
        #: The turn number and actions already probed in it (not saved: snapshots fall between turns).
        self._probed: tuple[int, set[str]] = (0, set())

    def fold(self, fact: Fact, turn: Turn | None = None) -> None:
        """Count ``fact`` (in ``turn``, when it is about one); a fact the diagnosis does not count changes nothing."""
        notice = _NOTICES.get(type(fact))
        if notice is not None:
            notice(self, fact, turn)

    # -- actions ---------------------------------------------------------------------

    def _answered(self, fact: Answered, turn: Turn | None) -> None:
        """A tool call returned: count it against its action (not a preview's, nor a name that is not text)."""
        if turn is not None and not turn.peek and isinstance(fact.name, str):
            self._called(turn, fact.name, fact.args, fact.result)

    def _called(self, turn: Turn, name: str, args: Any, result: ToolResult) -> None:
        """Count a finished tool call against its action; when it was refused, check whether any choice the tool
        offered could have worked."""
        env = turn.env
        if (result.data or {}).get("error") in _NOT_ABOUT_RULES:
            return
        if name not in env.contract.actions:
            return
        entry = self._action(name)
        entry["calls"] += 1
        if turn.stats.llm_calls:  # a model chose this call: its refusal says something about the tool
            entry["chosen"] += 1
            entry["chosen_refused"] += not result.ok
        if result.ok:
            entry["applied"] += int(not turn.staged)  # a sealed choice takes effect when it commits
            return
        entry["refused"] += 1
        _tally(entry["reasons"], result.text)
        if not self._first_probe(turn, name):
            return
        with env.world.luck.apart():  # the run's own question, not the agent's: what it reads is not the turn's
            found = usable(turn, name)
        if found is False:
            entry["unusable"] += 1
            _tally(entry["stuck"], result.text)

    def _committed(self, fact: Committed, turn: Turn | None) -> None:
        """A sealed choice took effect when the choices committed."""
        self._action(fact.action)["applied"] += 1

    def _commit_refused(self, fact: CommitRefused, turn: Turn | None) -> None:
        """A sealed choice accepted when submitted did not happen when the choices committed."""
        entry = self._action(fact.action)
        entry["refused"] += 1
        _tally(entry["reasons"], fact.text)
        if fact.left:
            entry["left"] = entry.get("left", 0) + 1

    def _faulted(self, fact: Faulted, turn: Turn | None) -> None:
        """A rule failed, or an invariant broke, while an agent's action applied (which was refused and undone)."""
        entry = self.faults.setdefault(fact.path, [0, fact.error])
        entry[0] += 1
        if fact.action is not None:
            self._action(fact.action)["faulted"] += 1

    def _policy_rule(self, fact: PolicyRule, turn: Turn | None) -> None:
        """A coded policy rule acted, or its call was refused: a refused choice of that action — and, when it was never
        sent (arguments the action does not accept), a refused call of it too."""
        entry = self.policy_rules.setdefault(fact.path, [0, 0, ""])
        if fact.refusal is None:
            entry[0] += 1
            return
        entry[1] += 1
        entry[2] = fact.refusal
        counts = self._action(fact.action)
        counts["chosen"] += 1
        counts["chosen_refused"] += 1
        if not fact.sent:
            counts["calls"] += 1
            counts["refused"] += 1
            _tally(counts["reasons"], fact.refusal)

    def _action(self, name: str) -> dict[str, Any]:
        return self.actions.setdefault(name, {"calls": 0, "refused": 0, "reasons": {}, "unusable": 0, "stuck": {},
                                              "applied": 0, "faulted": 0, "chosen": 0, "chosen_refused": 0})

    def _first_probe(self, turn: Turn, name: str) -> bool:
        if self._probed[0] != turn.number:
            self._probed = (turn.number, set())
        if name in self._probed[1]:
            return False
        self._probed[1].add(name)
        return True

    # -- stages and agents -----------------------------------------------------------

    def _stage(self, fact: StageVisit, turn: Turn | None) -> None:
        counts = self.stages.setdefault(fact.stage, [0, 0, 0, 0])
        counts[0] += fact.reached
        counts[1] += fact.ran
        counts[2] += fact.woke
        counts[3] += fact.capped

    def _offered(self, fact: Offered, turn: Turn | None) -> None:
        """A fresh turn's tools were read: note whether the agent had any action it could take, and if not why (not in
        a preview)."""
        if turn is None or turn.peek or turn.ledger.acted:
            return
        entry = self.agents.setdefault(turn.actor.entity_type,
                                       {"wakes": 0, "able": 0, "rounds": 0, "last_round": 0, "reasons": {}})
        entry["wakes"] += 1
        if entry["last_round"] != turn.round:
            entry["rounds"] += 1
            entry["last_round"] = turn.round
        if fact.has_action:
            entry["able"] += 1
            return
        env, actor = turn.env, turn.actor
        used_round = env.world.used_round.get(actor.id, {})
        for name in stage_actions(env.contract, turn.stage, actor.entity_type):
            why = env.actions.blocked(actor, name, turn.ledger.used, used_round, offered=True)
            if why:
                _tally(entry["reasons"], f"{name}: {why}")
                return
        _tally(entry["reasons"], f"none of the actions of stage {turn.stage.name} was offered")

    def _overwrote(self, fact: Overwrote, turn: Turn | None) -> None:
        entry = (self.loop_overwrites if fact.loop else self.overwrites).setdefault(fact.where, [0, fact.example])
        entry[0] += 1

    # -- saving ----------------------------------------------------------------------

    def copy(self, written: set[str]) -> Diagnosis:
        """The same counts, for a copy of the run whose world holds ``written``."""
        copied = Diagnosis(written)
        copied.actions, copied.stages, copied.agents = _copy(self.actions), _copy(self.stages), _copy(self.agents)
        copied.overwrites, copied.loop_overwrites = _copy(self.overwrites), _copy(self.loop_overwrites)
        copied.faults, copied.policy_rules = _copy(self.faults), _copy(self.policy_rules)
        copied.chance_woken = set(self.chance_woken)
        copied._probed = (self._probed[0], set(self._probed[1]))
        return copied

    def to_dict(self) -> dict[str, Any]:
        return {"actions": _copy(self.actions), "stages": _copy(self.stages), "agents": _copy(self.agents),
                "overwrites": _copy(self.overwrites), "loop_overwrites": _copy(self.loop_overwrites),
                "faults": _copy(self.faults), "policy_rules": _copy(self.policy_rules),
                "chance_woken": sorted(self.chance_woken), "written": sorted(self.written)}

    def load(self, data: dict[str, Any] | None) -> None:
        """Take the counts of :meth:`to_dict` (the written names in place: the world holds the same set)."""
        data = data or {}
        self.actions, self.stages = _copy(data.get("actions", {})), _copy(data.get("stages", {}))
        for entry in self.actions.values():  # snapshots from before these were counted
            for key in ("applied", "faulted", "chosen", "chosen_refused"):
                entry.setdefault(key, 0)
        for counts in self.stages.values():
            counts.extend([0] * (4 - len(counts)))
        self.agents, self.overwrites = _copy(data.get("agents", {})), _copy(data.get("overwrites", {}))
        self.loop_overwrites = _copy(data.get("loop_overwrites", {}))
        self.faults = _copy(data.get("faults", {}))
        self.policy_rules = _copy(data.get("policy_rules", {}))
        self.chance_woken = set(data.get("chance_woken", []))
        self.written.clear()
        self.written.update(data.get("written", []))


#: What each fact the diagnosis counts adds to it.
_NOTICES: dict[type, Callable[[Diagnosis, Any, Any], None]] = {
    Answered: Diagnosis._answered, Offered: Diagnosis._offered, StageVisit: Diagnosis._stage,
    Faulted: Diagnosis._faulted, Committed: Diagnosis._committed, CommitRefused: Diagnosis._commit_refused,
    PolicyRule: Diagnosis._policy_rule, Overwrote: Diagnosis._overwrote}


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def usable(turn: Turn, name: str) -> bool | None:
    """Whether any choice the action's tool offers right now would succeed: tried on a copy of the change that is
    rolled back. True as soon as one works; False only when every parameter could be tried and none worked; None
    when that cannot be told (the action is not offered, a parameter is free text or unbounded, or there are too
    many combinations)."""
    env, actor = turn.env, turn.actor
    if turn.ledger.actions_left <= 0 or name not in stage_actions(env.contract, turn.stage, actor.entity_type):
        return None
    if env.actions.blocked(actor, name, turn.ledger.used, env.world.used_round.get(actor.id, {})) is not None:
        return None
    found = _axes(env.information.tool(actor, name, turn.staged).input_schema)
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


def _axes(schema: dict[str, Any]) -> tuple[dict[str, list[Any]], bool] | None:
    """The values to try for each parameter, and whether they cover every parameter. None when a required parameter
    cannot be listed (nothing can be tried without it); an optional one that cannot be listed is left out, so trying
    the rest proves only that something works, never that nothing does."""
    required = set(schema.get("required") or [])
    axes: dict[str, list[Any]] = {}
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


def _values(prop: dict[str, Any]) -> list[Any] | None:
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

    def __init__(self, stage: str, facts: Facts):
        self.stage = stage
        self.facts = facts
        self.writer = ""
        self.action = ""
        self._last: dict[Any, Any] = {}

    def assigned(self, owner: Any, prop: str, rest: Sequence[Any], value: Any, source: str) -> None:
        """``owner.prop`` (at element path ``rest``) was assigned ``value`` by the current choice."""
        key = (owner.id if isinstance(owner, Entity) else "$world", prop, repr(list(rest)))
        before = self._last.get(key)
        self._last[key] = (self.writer, value)
        sides = _ASSIGNMENT.split(source, 1)
        builds_on_before = len(sides) == 2 and _whole(sides[0]) in sides[1]
        if before is None or before[0] == self.writer or _same(before[1], value) or builds_on_before:
            return  # the first write, the same agent again, the same value, or a change built on the value before
        shown = f"{owner.name or owner.id}.{prop}" if isinstance(owner, Entity) else f"$world.{prop}"
        self.facts.emit(Overwrote(self.stage, f"`{source}` in actions.{self.action} set {shown}, replacing the value "
                                              f"{before[0]}'s choice had set"))


class LoopWrites:
    """While an `each` loop runs (outside sealed commits): plain `=` writes to one target — not the loop's own item —
    from different items, when the loop reads that target nowhere else. Every item but the last is then lost."""

    def __init__(self, world: Any, path: str, body: str):
        self.world, self.path, self.body = world, path, body
        self.item: Any = None
        self.position = 0
        self._last: dict[Any, Any] = {}

    @classmethod
    def start(cls, world: Any, effect: dict[str, Any], path: str) -> LoopWrites | None:
        """Watch a loop's writes, unless the run keeps no diagnosis or writes are already watched."""
        if world.facts is None or world.watched_writes is not None:
            return None
        watch = world.watched_writes = cls(world, path, json.dumps(effect, default=str))
        return watch

    def assigned(self, owner: Any, prop: str, rest: Sequence[Any], value: Any, source: str) -> None:
        if owner is self.item:
            return
        target = _whole(_ASSIGNMENT.split(source, 1)[0])
        if self.body.count(target) > 1:
            return  # the loop reads the target too: a running best, a guard, a change built on it
        key = (owner.id if isinstance(owner, Entity) else "$world", prop, repr(list(rest)))
        before = self._last.get(key)
        self._last[key] = (self.position, value)
        if before is None or before[0] == self.position or _same(before[1], value):
            return
        self.world.facts.emit(Overwrote(self.path, f"`{source}` ran for several items with different values, so only "
                                                   "the last item's value is kept", loop=True))


def _whole(target: str) -> str:
    """An assignment's target without its element path: ``$who.tally`` for ``$who.tally[x]``, so reading the whole
    value (``$get($who.tally, x, 0)``) counts as reading the target."""
    return target.strip().split("[", 1)[0].rstrip()


def _same(a: Any, b: Any) -> bool:
    try:
        return bool(a == b)
    except Exception:  # values that cannot be compared are treated as different
        return False
