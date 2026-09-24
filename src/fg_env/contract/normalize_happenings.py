"""Earlier forms of what happens outside turns, and of time, rewritten into events and the current stage, action, view
and clock fields.

Triggers, stage hooks and type hooks become events on the matching anchor, appended after the events already
declared, so events keep their order and luck. An event's `phase`, `at`, `every` and `arms` become its `on` and `when`,
and its `each` loop moves into its `do`. What has no current form (continuous time, `on_wake`, `auto`, an action's
`duration`) is left as written, so the parser reports it with what to write instead."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .normalize import rule

__all__: list[str] = []

#: Stage hooks, the anchor each becomes, and the condition it ran under (the turn anchor fires after every turn).
_STAGE_HOOKS = (("on_enter", "start", None), ("on_exit", "end", None), ("on_timeout", "turn", "$timed_out"),
                ("on_idle", "turn", "not $acted"), ("on_turn_end", "turn", None))
#: An event's loop fields, which move into an `each` effect.
_LOOP = ("each", "as", "where", "order", "sync")


def _effects(value: Any) -> list[Any]:
    return [value] if isinstance(value, (str, Mapping)) else list(value or [])


def _all(conditions: list[str]) -> str | None:
    """Every condition AND-ed, in order (an expression stops at the first false one, as the fields it replaces did)."""
    if len(conditions) <= 1:
        return conditions[0] if conditions else None
    return " and ".join(f"({condition})" for condition in conditions)


def _listed(values: Any) -> str:
    return "[" + ", ".join(json.dumps(v) if not isinstance(v, str) else repr(v) for v in values) + "]"


def _append(data: dict[str, Any], added: list[dict[str, Any]]) -> None:
    """Append ``added`` to the events, each once: a contract whose mechanisms were expanded already holds them."""
    events = data.setdefault("events", []) if added else None
    if not isinstance(events, list):
        return
    held = {json.dumps(event, sort_keys=True, default=str) for event in events}
    events.extend(event for event in added if json.dumps(event, sort_keys=True, default=str) not in held)


@rule
def _triggers(data: dict[str, Any]) -> list[str]:
    """``triggers: [{when, do, …}]`` → ``events: [{on: "change", when, do, …}]``."""
    triggers = data.get("triggers")
    if not isinstance(triggers, list):
        return []
    events = data.setdefault("events", [])
    if not isinstance(events, list):
        return []
    for trigger in data.pop("triggers"):
        events.append({"on": "change", **trigger} if isinstance(trigger, Mapping) else trigger)
    return [f"triggers: {len(triggers)} became events on 'change'"] if triggers else []


@rule
def _event_timing(data: dict[str, Any]) -> list[str]:
    """An event's `phase`, `arms`, `at`, `every` and loop fields → its `on`, `when` and a `do` of one `each`."""
    events = data.get("events")
    if not isinstance(events, list):
        return []
    notes = []
    for index, event in enumerate(events):
        if isinstance(event, dict) and any(key in event for key in ("phase", "at", "every", "arms", "each")):
            events[index] = _timed(event)
            notes.append(f"events[{index}]: phase/at/every/arms/each became on, when and an each effect")
    return notes


def _timed(event: dict[str, Any]) -> dict[str, Any]:
    out = dict(event)
    phase = out.pop("phase", None)
    if phase is not None and "on" not in out:
        out["on"] = f"round.{phase}"
    conditions = []
    arms = out.pop("arms", None)
    if arms is not None:
        conditions.append(f"$arm in {_listed([arms] if isinstance(arms, str) else arms)}")
    at = out.pop("at", None)
    if isinstance(at, list):
        conditions.append(f"$round in {_listed(at)}")
    elif at is not None:
        conditions.append(f"$round == {at}")
    every = out.pop("every", None)
    if every is not None:
        conditions.append(f"($round - 1) % {every if isinstance(every, int) else f'({every})'} == 0")
    if out.get("when") is not None:
        conditions.append(out["when"])
    when = _all(conditions)
    if when is not None:
        out["when"] = when
    if "each" in out:
        out["do"] = [_loop(out)]
        for key in _LOOP:
            out.pop(key, None)
    return {key: out[key] for key in ("name", "on", "when", "do") if key in out} | out


def _loop(event: dict[str, Any]) -> dict[str, Any]:
    """The `each` effect an event's loop becomes; its `order` becomes a shuffled or sorted `each`."""
    items, order, name = event["each"], event.get("order"), event.get("as")
    loop: dict[str, Any] = {}
    if order == "random":
        items = f"$shuffle({items})"
    elif isinstance(order, str) and "$" in order:
        key = re.sub(rf"\${re.escape(name)}\b", "$it", order) if name else order
        items = f"$sort({items}, {key})"
    elif order is not None:
        loop["order"] = order  # not an order: left for the check to report
    loop = {"each": items, **loop}
    for key in ("as", "where"):
        if event.get(key) is not None:
            loop[key] = event[key]
    if event.get("sync"):
        loop["sync"] = True
    loop["do"] = _effects(event.get("do"))
    return loop


@rule
def _stage_hooks(data: dict[str, Any]) -> list[str]:
    """A stage's `on_enter`, `on_exit`, `on_idle`, `on_timeout` and `on_turn_end` → events on its anchors; `atomic`
    → `valid: "true"`; `time_limit` → left to the run (``env.run(time_limit=…)``)."""
    stages = data.get("stages")
    if not isinstance(stages, list):
        return []
    added: list[dict[str, Any]] = []
    notes = []
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
            continue
        name = stage["name"]
        timed = bool(_effects(stage.get("on_timeout")))
        for hook, point, condition in _STAGE_HOOKS:
            if hook not in stage:
                continue
            effects = _effects(stage.pop(hook))
            if not effects:
                continue
            event: dict[str, Any] = {"on": f"stage.{name}.{point}"}
            when = f"{condition} and not $timed_out" if hook == "on_idle" and timed else condition
            if when is not None:
                event["when"] = when
            event["do"] = effects
            added.append(event)
            notes.append(f"stages.{name}.{hook}: became an event on 'stage.{name}.{point}'")
        if "atomic" in stage:
            if stage.pop("atomic") and not stage.get("valid"):
                stage["valid"] = "true"
            notes.append(f"stages.{name}.atomic: became valid")
        if "time_limit" in stage:
            stage.pop("time_limit")
            notes.append(f"stages.{name}.time_limit: removed; limit turns with env.run(time_limit=...)")
        if stage.get("auto") is False:
            stage.pop("auto")
    _append(data, added)
    return notes


@rule
def _type_hooks(data: dict[str, Any]) -> list[str]:
    """A type's `on_create` / `on_remove` → events on `create.<type>` / `remove.<type>`. `on_create_at_build: false`
    keeps it off the entities built before round 1."""
    types = data.get("types")
    if not isinstance(types, Mapping):
        return []
    added: list[dict[str, Any]] = []
    notes = []
    skipped = _built_without_hooks(types)
    for type_name, spec in types.items():
        if not isinstance(spec, dict):
            continue
        spec.pop("on_create_at_build", None)
        for hook, anchor in (("on_create", "create"), ("on_remove", "remove")):
            if hook not in spec:
                continue
            effects = _effects(spec.pop(hook))
            if not effects:
                continue
            event: dict[str, Any] = {"on": f"{anchor}.{type_name}"}
            quiet = [kind for kind in skipped if type_name in _lineage(types, kind)]
            if hook == "on_create" and quiet:
                every = all(type_name not in _lineage(types, kind) or kind in quiet for kind in types)
                event["when"] = "$round >= 1" if every else f"$round >= 1 or not ($it.type in {_listed(quiet)})"
            event["do"] = effects
            added.append(event)
            notes.append(f"types.{type_name}.{hook}: became an event on '{anchor}.{type_name}'")
    _append(data, added)
    return notes


def _lineage(types: Mapping[str, Any], name: Any) -> list[str]:
    """``name`` and its ancestors (``extends``), nearest first; stops at unknown types and cycles."""
    chain: list[str] = []
    while isinstance(name, str) and name in types and name not in chain:
        chain.append(name)
        spec = types[name]
        name = spec.get("extends") if isinstance(spec, Mapping) else None
    return chain


def _built_without_hooks(types: Mapping[str, Any]) -> list[str]:
    """The types whose entities made at build ran no `on_create` (the nearest `on_create_at_build` in the lineage)."""
    def at_build(kind: str) -> bool:
        for name in _lineage(types, kind):
            spec = types[name]
            if isinstance(spec, Mapping) and "on_create_at_build" in spec:
                return bool(spec["on_create_at_build"])
        return True

    return [kind for kind in types if not at_build(kind)]


@rule
def _actions(data: dict[str, Any]) -> list[str]:
    """An action's `private: true` → `announce: false`; `chance` / `otherwise` → an `if` over `$chance`; `tool` →
    removed (every action is its own tool)."""
    actions = data.get("actions")
    if not isinstance(actions, Mapping):
        return []
    notes = []
    for name, spec in actions.items():
        if not isinstance(spec, dict):
            continue
        if "private" in spec:
            if spec.pop("private") is True:
                spec["announce"] = False
            notes.append(f"actions.{name}.private: became announce")
        if "chance" in spec or "otherwise" in spec:
            chance, otherwise = spec.pop("chance", None), _effects(spec.pop("otherwise", None))
            if chance is not None:
                branch: dict[str, Any] = {"if": f"$chance({chance})", "then": _effects(spec.get("do"))}
                if otherwise:
                    branch["else"] = otherwise
                spec["do"] = [branch]
            notes.append(f"actions.{name}.chance: became an if over $chance in do")
        if "tool" in spec:
            spec.pop("tool")
            notes.append(f"actions.{name}.tool: removed")
    return notes


@rule
def _views(data: dict[str, Any]) -> list[str]:
    """A view's `stages: [...]` → `when: "$stage in [...]"`; `only_changes` → removed."""
    views = data.get("views")
    if not isinstance(views, Mapping):
        return []
    notes = []
    for name, spec in views.items():
        if not isinstance(spec, dict):
            continue
        if "stages" in spec:
            stages = spec.pop("stages")
            if stages is not None:
                spec["when"] = _all([f"$stage in {_listed(stages)}",
                                     *([spec["when"]] if spec.get("when") is not None else [])])
            notes.append(f"views.{name}.stages: became when")
        if "only_changes" in spec:
            spec.pop("only_changes")
            notes.append(f"views.{name}.only_changes: removed")
    return notes


@rule
def _clock(data: dict[str, Any]) -> list[str]:
    """``clock.mode: "rounds"`` and the continuous-only fields beside it → removed (every clock counts rounds)."""
    clock = data.get("clock")
    if not isinstance(clock, dict) or clock.get("mode", "rounds") != "rounds":
        return []
    dropped = [key for key in ("mode", "tick", "jump", "horizon") if clock.pop(key, None) is not None]
    return [f"clock.{key}: removed" for key in dropped]
