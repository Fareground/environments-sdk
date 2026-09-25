"""Noninterference: what an agent must not know never shows in anything it is shown, offered or charged.

For an observer agent, two worlds are played from the same seed: the run as it is, and the same run with everything
hidden from the observer changed before the first round — other entities' private properties it may not read, the
world's private properties, an entry in each record that only another agent sees — and, in a second pass, other
agents' sealed choices. Every other agent repeats in the second world the calls it made in the first (a sealed choice
aside, in that pass), and so does the observer. At each of the observer's turns, while what it may see of the world is
the same in both (:func:`seen`), everything it is shown must be byte-identical: its brief, its update (announcements
included), its tools and their schemas, its legal calls (``fg_env.rl.game``'s dry run), every view it may look at and
every entity it may inspect, and each call's reply — its text, whether it ended the turn, and whether it was charged.

The documented reveals are where the comparison stops, never where it fails:

* the world the observer may see differs — game logic wrote what it worked out from a hidden value into something the
  observer sees (a public property, an entry it reads, news), the contract's own way to reveal it;
* a call's reply differs and neither reply is a free refusal: the observer's action applied, or was refused and
  charged for it (it read something hidden or drew luck), so it paid for what it learned.

A free refusal that differs, or a difference in anything shown while the visible world is the same, is a leak.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import fg_env
from fg_env.game.space import legal_calls
from fg_env.participants import RandomAgent

#: Legal calls listed per action at most (a larger space is noted as unlisted, in both worlds alike).
LIMIT = 64
#: A text a hidden entry carries, and what a hidden text becomes.
HIDDEN_TEXT = "zqhiddenzq"


@dataclass
class Turn:
    """One turn of the observer: what it could see of the world, what it was shown, and the replies to its calls."""

    seen: str
    shown: dict[str, Any]
    replies: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Play:
    """A played world: every agent's calls turn by turn, and the observer's turns."""

    calls: dict[str, list[list[tuple[str, dict[str, Any]]]]]
    turns: list[Turn]
    status: str


def _plain(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def seen(env: fg_env.Env, observer: str) -> str:
    """What ``observer`` may see of the world, as one text: every property not hidden from it, the entries and events
    it may know of, where the run is. An action's announcement counts by who did what and whether it applied — its
    default line and its arguments are the engine's words, held to the observer's view, not part of the world; an
    `announce` the contract writes is the contract's."""
    world = env.world
    reader = world.entities[observer]
    evaluation = world.evaluation
    entities = [[e.id, e.entity_type, e.alive, e.location_id,
                 {key: value for key, value in e.properties.items() if not world.hides(e, key, reader)}]
                for e in world.entities.values()]
    props = {key: value for key, value in world.props.items() if not world.hidden.world_hides(key, reader)}
    # An entry's `seq` counts every entry posted, seen or not: bookkeeping, like an event's, left out here (a text that
    # shows one is held to the comparison).
    records = {name: [{k: v for k, v in entry.items() if k != "seq"}
                      for entry in rows if evaluation.entry_visible(name, entry, reader)]
               for name, rows in world.records_store.items()}
    events = []
    for event in world.log:
        if not evaluation.event_visible(event, reader):
            continue
        if event.kind == "action":
            spec = env.contract.actions.get(event.data.get("action"))
            written = spec is not None and isinstance(spec.announce, str)
            events.append([event.round, event.actor, event.data.get("action"), event.data.get("success"),
                           event.text if written else None])
        else:
            events.append([event.round, event.kind, event.text, event.actor,
                           {k: v for k, v in event.data.items() if not (event.kind == "record" and k == "entry")}])
    links = {kind: sorted(map(list, edges.items())) for kind, edges in world.links.items()}
    return _plain([world.round, world.stage, entities, props, records, events, links, world.end_request])


def _shown(env: fg_env.Env, wake: Any) -> dict[str, Any]:
    """Everything ``wake``'s agent is shown as its turn begins, reads included."""
    calls, unlisted = legal_calls(env, wake._turn, limit=LIMIT)
    shown: dict[str, Any] = {
        "brief": wake.brief, "update": wake.update, "tools": _tools(wake),
        "legal": sorted(_plain(call) for call in calls), "unlisted": unlisted,
    }
    reads = []
    for tool in wake.tools:
        if tool.name in ("look", "inspect"):
            arg = "view" if tool.name == "look" else "id"
            prop = next(iter(tool.input_schema.get("properties", {}).values()), {})
            reads += [(tool.name, {arg: choice}) for choice in prop.get("enum", [])]
    texts = []
    for name, args in reads:
        result = wake.call(name, args)
        texts.append([name, args, result.ok, result.text])
        if not result.ok:
            break
    shown["reads"] = texts
    return shown


def _tools(wake: Any) -> list[Any]:
    return [[tool.name, tool.description, _plain(tool.input_schema)] for tool in wake.tools]


def _reply(result: Any, wake: Any) -> dict[str, Any]:
    return {"ok": result.ok, "text": result.text, "ended": result.ended, "data": _plain(result.data),
            "spent": _charged(result), "tools": _tools(wake) if not result.ended else None}


def _charged(result: Any) -> bool:
    """Whether a reply is a refusal the agent paid for: spent, or a turn undone and ended by what it could not see."""
    data = result.data or {}
    return bool(data.get("spent")) or (data.get("error") == "undone" and result.ended)


class _Player:
    """Plays every agent: in the first world at random, recording its calls; in the second, repeating them (the
    agents but the observer choose sealed choices afresh). Records the observer's turns."""

    concurrent = False

    def __init__(self, env: fg_env.Env, observer: str, seed: int, replay: Play | None, sealed: bool):
        self.env, self.observer, self.replay, self.sealed = env, observer, replay, sealed
        self.random = RandomAgent(seed=seed if replay is None else seed + 7919)
        self.calls: dict[str, list[list[tuple[str, dict[str, Any]]]]] = {}
        self.turns: list[Turn] = []
        self.simultaneous = {stage.name for stage in env.contract.stage_list() if stage.turns == "simultaneous"}

    def __call__(self, wake: Any) -> None:
        agent = wake.entity_id
        mine = self.calls.setdefault(agent, [])
        mine.append([])
        watching = agent == self.observer
        turn = Turn(seen(self.env, agent), _shown(self.env, wake)) if watching else None
        if turn is not None:
            self.turns.append(turn)
        planned = self._planned(agent, len(mine) - 1, wake)
        if planned is None:
            self.random(_Recorded(wake, mine[-1], turn))
            return
        for name, args in planned:
            if wake.done:
                break
            result = wake.call(name, args)
            mine[-1].append((name, args))
            if turn is not None:
                turn.replies.append(_reply(result, wake))
        if not wake.done:
            wake.end()

    def _planned(self, agent: str, index: int, wake: Any) -> list[tuple[str, dict[str, Any]]] | None:
        """The calls to repeat, or None to play at random (the first world, or another's sealed choice)."""
        if self.replay is None:
            return None
        if self.sealed and agent != self.observer and wake.stage in self.simultaneous:
            return None
        turns = self.replay.calls.get(agent, [])
        return turns[index] if index < len(turns) else []


class _Recorded:
    """A wake whose calls are recorded (and the replies, for the observer)."""

    def __init__(self, wake: Any, calls: list[tuple[str, dict[str, Any]]], turn: Turn | None):
        self._wake, self._calls, self._record = wake, calls, turn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wake, name)

    def call(self, name: str, args: Any = None) -> Any:
        result = self._wake.call(name, args)
        self._calls.append((name, dict(args or {})))
        if self._record is not None:
            self._record.replies.append(_reply(result, self._wake))
        return result


def play(source: Any, seed: int, observer: str, inputs: Any = None, replay: Play | None = None,
         rounds: int | None = None, sealed: bool = False) -> Play:
    """Play ``source`` watching ``observer``: at random, or — given the first world's play to ``replay`` — as the second
    world, with what the observer must not know changed (and, with ``sealed``, other agents' sealed choices too)."""
    env = fg_env.load(source, seed=seed, inputs=inputs)
    if replay is not None:
        hide_otherwise(env, observer, random.Random(seed))
    player = _Player(env, observer, seed, replay, sealed)
    result = env.run(player, rounds=rounds)
    return Play(player.calls, player.turns, result.status)


# -- what the observer must not know, changed -----------------------------------------------------------------------


def hide_otherwise(env: fg_env.Env, observer: str, rng: random.Random) -> None:
    """Change everything hidden from ``observer`` before the first round (see the module docstring)."""
    world = env.world
    reader = world.entities[observer]
    pools: dict[tuple[str, str], list[Any]] = {}  # the values each property takes across its type: its domain
    for entity in world.entities.values():
        for key, value in entity.properties.items():
            pools.setdefault((entity.entity_type, key), []).append(value)
    for entity in list(world.entities.values()):
        written = _written(env, entity.entity_type)
        for key, value in list(entity.properties.items()):
            if key in written and world.hides(entity, key, reader):
                spec = world.type_props[entity.entity_type][key]
                changed = _other(spec, value, pools[(entity.entity_type, key)], rng)
                if changed is not None:
                    _kept(env, partial(world.set_prop, entity, key), changed, value)
    for key, value in list(world.props.items()):
        if world.hidden.world_hides(key, reader):
            changed = _other(env.contract.world[key], value, [value], rng)
            if changed is not None:
                _kept(env, partial(world.set_world, key), changed, value)
    others = [e.id for e in world.entities.values() if e.id != observer and env.contract.is_agent(e.entity_type)]
    if others:
        for name, spec in env.contract.records.items():
            fields = {key: _blank(kind) for key, kind in spec.fields.items()}
            author = rng.choice(others)
            world.post(name, fields, author, (author,), f"records.{name}")
    world.commit()


def _kept(env: fg_env.Env, write: Any, changed: Any, value: Any) -> None:
    """``write`` ``changed`` in place of ``value``, unless the contract's invariants (a conservation law over hidden
    values) forbid it."""
    write(changed)
    try:
        env.rules.check_invariants("the perturbation", "build")
    except (fg_env.RunError, ValueError):
        write(value)


def _written(env: fg_env.Env, kind: str) -> set[str]:
    """The properties the contract's author declared on ``kind`` and its ancestors: a mechanism's bookkeeping (an
    auction's escrow) is kept consistent by the mechanism, so changing it alone would break the rules, not test them."""
    types = (env.contract._source or {}).get("types") or {}
    return {prop for name in env.contract.lineage(kind) for prop in ((types.get(name) or {}).get("props") or {})}


def _other(spec: Any, value: Any, pool: list[Any], rng: random.Random) -> Any:
    """A different value ``spec`` allows — another its type's entities hold (``pool``) when there is one, so it stays
    in the property's domain (a probability, a card) — or None when there is none to pick."""
    others = [item for item in pool if item != value and type(item) is type(value)]
    if others:
        return rng.choice(others)
    kind = spec.type
    if isinstance(value, bool):
        return not value
    if kind == "enum" and isinstance(spec.values, list):
        options = [v for v in spec.values if v != value]
        return rng.choice(options) if options else None
    if isinstance(value, int):
        low = spec.min if isinstance(spec.min, (int, float)) else None
        high = spec.max if isinstance(spec.max, (int, float)) else None
        return next((changed for changed in (value + 1, value - 1)
                     if (low is None or changed >= low) and (high is None or changed <= high)), None)
    if isinstance(value, str):
        return value + HIDDEN_TEXT if kind == "text" else None  # an id or an asset must name one
    if isinstance(value, list) and len(value) > 1 and value != list(reversed(value)):
        return list(reversed(value))
    return None


def _blank(kind: str) -> Any:
    return {"text": HIDDEN_TEXT, "int": 0, "number": 0, "bool": False}.get(kind)


# -- the comparison --------------------------------------------------------------------------------------------------


def leaks(first: Play, second: Play, observer: str) -> list[str]:
    """Where the observer's turns in ``second`` differ from ``first`` while nothing documented reveals why."""
    found: list[str] = []
    for index, (a, b) in enumerate(zip(first.turns, second.turns)):
        if a.seen != b.seen:
            return found  # game logic showed it what it worked out: a reveal the contract makes
        for key in a.shown:
            if a.shown[key] != b.shown[key]:
                found.append(f"{observer}, turn {index + 1}: its {key} differs: {_first(a.shown[key], b.shown[key])}")
        if found:
            return found
        for number, (x, y) in enumerate(zip(a.replies, b.replies)):
            if x == y:
                continue
            free = [reply for reply in (x, y) if not reply["ok"] and not reply["spent"]]
            if free:
                found.append(f"{observer}, turn {index + 1}, call {number + 1}: a free reply differs: "
                             f"{_first(x, y)}")
            return found  # otherwise it applied, or it paid for what it learned
        if len(a.replies) != len(b.replies):
            return found
    return found


def _first(a: Any, b: Any) -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        key = next((k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)), None)
        return f"[{key}] {str(a.get(key))[:300]!r} vs {str(b.get(key))[:300]!r}"
    return f"{str(a)[:300]!r} vs {str(b)[:300]!r}"
