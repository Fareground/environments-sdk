"""Agent memory and recaps.

``memory`` keeps, per agent, what it read and did each round, its own notes and host-written reflections, each with an
importance that fades with a half-life; ``recall(query)`` retrieves by relevance (lexical by default, or host-scored),
recency and importance, and strengthens what it returns; ``$memories`` gives a compact view within a token budget.
``recap`` writes a "story so far" entry for a long record every N rounds (the host family's ``recap`` mode). Everything
lives in journaled state (private entity properties, records), so snapshots restore memory exactly.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import RunError
from ..expr import Call, ExprError, Untrusted, function
from ..expr.template import format_value
from ..host.common import MODEL_HINT, NAME, agents_of, clip, config_of, prop_of, type_list
from ..host.protocols import HostError
from ..host.tape import consult, plain, tape_prop
from ..registry import MechanismError, family_action, mode, use_key
from ..world.entity import Entity

__all__ = ["MemoryConfig", "RecapConfig", "lexical_relevance"]

DEFAULT_IMPORTANCE = {"did": 0.5, "saw": 0.3, "note": 0.8, "reflection": 0.9}
DEFAULT_WEIGHTS = {"relevance": 1.0, "recency": 1.0, "importance": 1.0}
CHARS_PER_TOKEN = 4
MAX_ENTRY_CHARS = 2000
#: Most recent memories a reflection reads.
REFLECTION_WINDOW = 20
MEMORY = "mind.memory"
RECAP = "host.recap"
_STOP = frozenset("a an and are as at be but by did do for from had has have he her his i if in into is it its "
                  "me my no not of on or our she so than that the their them then there they this to was we "
                  "were what when which who will with you your".split())

Memory = dict[str, Any]


class MemoryConfig(BaseModel):
    """Per-agent memory."""

    model_config = ConfigDict(extra="forbid")

    who: str | list[str] = Field(..., description="Agent type(s) that remember.")
    capture: list[Literal["did", "saw"]] = Field(["did", "saw"],
                                                 description="What is remembered each round: did (own actions), saw "
                                                             "(news the agent read).")
    note: str | None = Field("note", description="Name of the note tool (null: no notes).")
    recall: str | None = Field("recall", description="Name of the recall tool (null: no recall).")
    stages: list[str] | None = Field(None,
                                     description="Stages where note and recall are offered (default: every stage whose "
                                                 "actions include them).")
    max_chars: int = Field(500, ge=1, le=MAX_ENTRY_CHARS, description="Longest note, in characters.")
    recall_limit: int = Field(5, ge=1, le=50, description="Memories one recall returns.")
    half_life: float = Field(10.0, gt=0, description="Rounds (or clock time) after which recency halves.")
    importance: dict[str, float] = Field(default_factory=dict,
                                         description="Importance 0–1 per kind (did, saw, note, reflection).")
    weights: dict[str, float] = Field(default_factory=dict,
                                      description="Recall weights of relevance, recency, importance.")
    limit: int = Field(200, ge=1, le=5000, description="Memories kept per agent; the faintest are forgotten.")
    budget: int = Field(300, ge=10, le=20_000, description="Tokens of memory shown in the memory view.")
    views: bool = Field(True, description="Show the strongest memories in every update.")
    relevance: Literal["lexical", "host"] = Field("lexical",
                                                  description="How recall scores relevance: lexical (no host) or host "
                                                              "(a Ranker).")
    host: str | None = Field(None, description="Host ranker name (relevance: host).")
    reflect_every: int | None = Field(None, ge=1, description="Write a reflection with a host writer every N rounds.")
    reflect_host: str = Field("writer", description="Host writer for reflections.")
    reflect_prompt: str = Field("Reflect on these memories: what matters most now, and what should you remember going "
                                "forward? Answer in two or three sentences.", description="What a reflection asks for.")
    reflect_fallback: Literal["skip"] | None = Field(None,
                                                     description="Without a writer: skip reflections (default: stop "
                                                                 "with an error).")

    @field_validator("importance")
    @classmethod
    def _importance(cls, value: dict[str, float]) -> dict[str, float]:
        for kind, number in value.items():
            if kind not in DEFAULT_IMPORTANCE or not 0 <= number <= 1:
                raise ValueError(f"importance is {{kind: 0..1}} for {', '.join(DEFAULT_IMPORTANCE)}")
        return value

    @field_validator("weights")
    @classmethod
    def _weights(cls, value: dict[str, float]) -> dict[str, float]:
        for key, number in value.items():
            if key not in DEFAULT_WEIGHTS or number < 0:
                raise ValueError(f"weights is {{name: number ≥ 0}} for {', '.join(DEFAULT_WEIGHTS)}")
        return value

    def importance_of(self, kind: str) -> float:
        return self.importance.get(kind, DEFAULT_IMPORTANCE[kind])

    def weight(self, key: str) -> float:
        return self.weights.get(key, DEFAULT_WEIGHTS[key])


@mode("mind", "memory", MemoryConfig,
           "Per-agent memory: each round what the agent did and read is remembered, plus `note(text)` entries "
           "and optional host reflections; importance fades with `half_life`. `recall(query)` returns the most "
           "relevant memories (lexical, or host-scored) and strengthens them; a view shows the strongest within "
           "`budget` tokens ($memories). Stored in the private property <name> of each agent.",
           example={"who": "panelist", "half_life": 5, "budget": 250})
def _expand_memory(name: str, config: MemoryConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    agents = type_list(contract, config.who, "who")
    by: str | list[str] = agents if len(agents) > 1 else agents[0]
    for key in ("note", "recall"):
        tool = getattr(config, key)
        if tool is not None and not NAME.match(tool):
            raise MechanismError(f"{key} must be a tool name, got {tool!r}", None, key)
    if config.relevance == "host" and not config.host:
        raise MechanismError("relevance: host needs `host` (a Ranker)", None, "host")
    fragment: dict[str, Any] = {
        "types": {t: {"props": {name: {"type": "list", "default": [], "private": True},
                                f"{name}_seq": {"type": "int", "default": 0, "private": True},
                                f"{name}_recalled": {"type": "text", "default": "", "private": True}}} for t in agents},
        "world": {"host_tape": tape_prop(), f"{name}_cursor": {"type": "int", "default": 0}},
        "actions": {}, "events": [], "views": {},
    }
    actions = fragment["actions"]
    if config.note:
        actions[config.note] = {
            "by": by, "description": "Write a note to your future self. Only you can read it; it stays in your memory.",
            "params": {"text": {"type": "text", "max_len": config.max_chars, "description": "The note."}},
            "private": True, "do": [{"mind": name, "action": "note", "text": "$params.text"}], "outcome": "Noted."}
    if config.recall:
        actions[config.recall] = {
            "by": by,
            "description": "Search your memory for what bears on a question; returns the most relevant memories.",
            "params": {"query": {"type": "text", "max_len": 300, "description": "What you want to remember."}},
            "private": True, "do": [{"mind": name, "action": "recall", "query": "$params.query"}],
            "outcome": f"{{$actor.{name}_recalled}}"}
    if config.stages and actions:
        condition = {"expr": f"$stage in {json.dumps(config.stages)}", "why": "Not available now."}
        for action in actions.values():
            action["when"] = [condition]
        fragment["stage_hooks"] = {stage: {"actions": list(actions)} for stage in config.stages}
    if config.capture:
        fragment["events"].append({"name": f"{name}_capture", "phase": "end",
                                   "do": [{"mind": name, "action": "capture"}]})
    if config.reflect_every:
        fragment["events"].append({"name": f"{name}_reflect", "phase": "end",
                                   "when": f"$round % {config.reflect_every} == 0",
                                   "do": [{"mind": name, "action": "reflect"}]})
    if config.views:
        fragment["views"][name] = {"for": by, "title": "From your memory", "of": f"$memories($actor, '{name}')",
                                   "show": "{$it.label}: {$it.text}"}
    return fragment


# -- storage ----------------------------------------------------------------------


def _now(world: Any) -> float:
    return float(world.time) if world.continuous else float(world.round)


def _recency(entry: Memory, now: float, half_life: float) -> float:
    age = max(0.0, now - max(float(entry.get("at", 0)), float(entry.get("last", 0))))
    return float(0.5 ** (age / half_life))


def _retention(entry: Memory, now: float, config: MemoryConfig) -> float:
    return float(entry["importance"]) * _recency(entry, now, config.half_life)


def _entries(agent: Entity, name: str) -> list[Memory]:
    return list(prop_of(agent, name, []))


def _add(world: Any, agent: Entity, name: str, config: MemoryConfig, items: Sequence[tuple[str, str]]) -> None:
    if not items:
        return
    entries = _entries(agent, name)
    seq = int(prop_of(agent, f"{name}_seq", 0))
    now = _now(world)
    for kind, text in items:
        seq += 1
        entries.append({"id": seq, "round": world.round, "at": now, "kind": kind, "text": clip(text, MAX_ENTRY_CHARS),
                        "importance": config.importance_of(kind), "recalls": 0, "last": now})
    if len(entries) > config.limit:
        kept = sorted(entries, key=lambda e: (-_retention(e, now, config), -e["id"]))[: config.limit]
        entries = sorted(kept, key=lambda e: e["id"])
    world.set_prop(agent, name, entries)
    world.set_prop(agent, f"{name}_seq", seq)


def _label(entry: Memory) -> str:
    return f"Round {entry['round']} ({entry['kind']})"


# -- relevance ----------------------------------------------------------------------


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", str.__str__(text).lower())
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if len(w) > 1 and w not in _STOP]


def lexical_relevance(query: str, texts: Sequence[str]) -> list[float]:
    """Relevance 0–1 of each text to the query: IDF-weighted term matches, scaled to the best match."""
    wanted = set(_terms(query))
    docs = [Counter(_terms(t)) for t in texts]
    if not wanted or not docs:
        return [0.0] * len(docs)
    n = len(docs)
    idf = {term: math.log(1 + n / (1 + sum(1 for d in docs if term in d))) for term in wanted}
    raw = [sum(idf[t] * d[t] / (d[t] + 1) for t in wanted if t in d) for d in docs]
    best = max(raw)
    return [round(r / best, 6) if best > 0 else 0.0 for r in raw]


def _ranks(answer: Any, count: int) -> list[float]:
    if not isinstance(answer, list) or len(answer) != count:
        raise HostError(f"a ranking is a list of {count} numbers")
    for value in answer:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise HostError(f"relevance scores are numbers from 0 to 1, got {value!r}")
    return [float(v) for v in answer]


def _relevance(world: Any, name: str, config: MemoryConfig, agent: Entity, query: str,
               entries: list[Memory]) -> list[float]:
    texts = [str.__str__(e["text"]) for e in entries]
    if config.relevance == "lexical":
        return lexical_relevance(query, texts)
    request = plain({"query": query, "items": [{"id": e["id"], "text": t} for e, t in zip(entries, texts)]})
    ranked: list[float] = consult(world, service=config.host or "", method="rank", site=f"mechanisms.{name}",
                                  actor=agent.id, identity={"query": query, "ids": [e["id"] for e in entries]},
                                  ask=lambda adapter: list(adapter.rank(request)),
                                  validate=partial(_ranks, count=len(entries)))
    return ranked


# -- the mind op's memory actions ----------------------------------------------------------


def _actor(vars: dict[str, Any], action: str, where: str) -> Entity:
    actor = vars.get("actor")
    if not isinstance(actor, Entity):
        raise RunError(f"`{action}` runs inside an action (it needs $actor)", where)
    return actor


@family_action("mind", ("memory",), "capture", internal=True,
               example='{"mind": "memory", "action": "capture"}  (remember what each agent did and read since the last '
                       'capture)')
def _capture(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    from ..runtime.perception import Perception

    world = runner.world
    name = effect["mind"]
    config = config_of(world, name, MEMORY, MemoryConfig, where)
    cursor = int(world.props.get(f"{name}_cursor") or 0)
    events = [e for e in world.log if e.seq > cursor]
    perception = Perception(world.contract, world)
    lookups = [n for n, raw in world.contract.mechanisms.items() if use_key(raw) == "host.tool"]
    tools = {config.note, config.recall, *lookups}
    for agent in agents_of(world, config.who):
        items: list[tuple[str, str]] = []
        for event in events:
            if event.kind == "end" or not event.visible_to(agent.id):
                continue
            if event.kind == "action" and event.actor == agent.id:
                if "did" in config.capture and event.data.get("action") not in tools:
                    items.append(("did", _did(event)))
                continue
            if "saw" in config.capture:
                line = perception._event_line(event, agent)
                if line:
                    items.append(("saw", line))
        if "did" in config.capture:
            for lookup in lookups:
                evidence = prop_of(agent, f"{lookup}_evidence", []) if f"{lookup}_evidence" in agent.properties else []
                items += [("did",
                           f"You looked up {format_value(item['query'])} with {lookup}: {format_value(item['text'])}")
                          for item in evidence if item.get("round") == world.round]
        _add(world, agent, name, config, items)
    if events:
        world.set_world(f"{name}_cursor", events[-1].seq)


def _did(event: Any) -> str:
    action = str(event.data.get("action") or "act").replace("_", " ")
    params = event.data.get("params") or {}
    args = ", ".join(f"{key}={format_value(value)}" for key, value in params.items() if value is not None)
    failed = "" if event.data.get("success", True) else " (it failed)"
    return f"You did: {action}" + (f" ({args})" if args else "") + failed


@family_action("mind", ("memory",), "note", keys=("text",), required=("text",),
               example='{"mind": "memory", "action": "note", "text": "$params.text"}  (add a note to the actor\'s '
                       'memory)')
def _note(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["mind"]
    config = config_of(world, name, MEMORY, MemoryConfig, where)
    actor = _actor(vars, "note", where)
    text = runner.eval(effect["text"], vars)
    if not isinstance(text, str) or not text.strip():
        raise RunError(f"a note is non-empty text, got {format_value(text)}", where)
    _add(world, actor, name, config, [("note", clip(text, config.max_chars))])


@family_action("mind", ("memory",), "recall", keys=("query",), required=("query",),
               example='{"mind": "memory", "action": "recall", "query": "$params.query"}  (the actor\'s most relevant '
                       "memories as text in $actor.memory_recalled; recalled memories strengthen)")
def _recall(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["mind"]
    config = config_of(world, name, MEMORY, MemoryConfig, where)
    actor = _actor(vars, "recall", where)
    query = runner.eval(effect["query"], vars)
    if not isinstance(query, str):
        raise RunError(f"`query` must be text, got {format_value(query)}", where)
    entries = _entries(actor, name)
    if not entries:
        world.set_prop(actor, f"{name}_recalled", "Your memory is empty so far.")
        return
    now = _now(world)
    relevance = _relevance(world, name, config, actor, query, entries)
    scored = [(config.weight("relevance") * rel + config.weight("recency") * _recency(e, now, config.half_life)
               + config.weight("importance") * float(e["importance"]), int(e["id"]), rel)
              for e, rel in zip(entries, relevance)]
    matched = any(rel > 0 for _, _, rel in scored)
    pool = [s for s in scored if s[2] > 0] if matched else scored
    chosen = {entry_id for _, entry_id, _ in sorted(pool, key=lambda s: (-s[0], -s[1]))[: config.recall_limit]}
    updated = [{**e, "recalls": int(e["recalls"]) + 1, "last": now} if e["id"] in chosen else e for e in entries]
    world.set_prop(actor, name, updated)
    asked = format_value(Untrusted(query))
    header = (f"Memories about {asked}:" if matched
              else f"Nothing in your memory matches {asked}; your strongest memories:")
    lines = [header] + [f"- {_label(e)}: {format_value(e['text'])}" for e in updated if e["id"] in chosen]
    world.set_prop(actor, f"{name}_recalled", "\n".join(lines))


def _skip() -> None:
    return None


@family_action("mind", ("memory",), "reflect", internal=True,
               example='{"mind": "memory", "action": "reflect"}  (each agent reflects on its recent memories with the '
                       'host writer)')
def _reflect(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["mind"]
    config = config_of(world, name, MEMORY, MemoryConfig, where)
    for agent in agents_of(world, config.who):
        entries = _entries(agent, name)
        if not entries:
            continue
        recent = entries[-REFLECTION_WINDOW:]
        request = plain({"task": "reflection", "prompt": config.reflect_prompt,
                         "agent": {"id": agent.id, "name": agent.name}, "memories": [e["text"] for e in recent]})
        text = consult(world, service=config.reflect_host, method="write", site=f"mechanisms.{name}", actor=agent.id,
                       identity={"through": recent[-1]["id"]}, ask=partial(_ask_write, request),
                       validate=partial(_text, limit=MAX_ENTRY_CHARS),
                       fallback=_skip if config.reflect_fallback == "skip" else None)
        if text is not None:
            _add(world, agent, name, config, [("reflection", Untrusted(text))])


def _ask_write(request: dict[str, Any], adapter: Any) -> Any:
    return adapter.write(request)


def _text(answer: Any, limit: int) -> str:
    if not isinstance(answer, str) or not answer.strip():
        raise HostError("the answer must be non-empty text")
    return clip(answer.strip(), limit)


@function("memories(agent, name, budget?)",
          "The agent's strongest memories from the memory mechanism `name`, oldest first, within `budget` tokens "
          "(default: the mechanism's budget): a list of {id, round, kind, text, label}.",
          min_args=2, max_args=3)
def _memories_function(call: Call) -> list[Memory]:
    world: Any = call.scope.world
    agent = world.entity(call.arg(0))
    name = call.arg(1)
    if agent is None:
        raise ExprError(f"$memories: expected an agent, got {format_value(call.arg(0))}", call.source)
    raw = world.contract.mechanisms.get(name) if isinstance(name, str) else None
    if use_key(raw) != MEMORY:
        raise ExprError(f"$memories: '{name}' is not a declared mind (memory) mechanism", call.source)
    config = config_of(world, name, MEMORY, MemoryConfig, "memories")
    budget = call.arg(2, config.budget)
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        raise ExprError(f"$memories: budget must be a number of tokens > 0, got {budget!r}", call.source)
    now = _now(world)
    left = int(budget * CHARS_PER_TOKEN)
    chosen: list[Memory] = []
    for entry in sorted(_entries(agent, name), key=lambda e: (-_retention(e, now, config), -e["id"])):
        size = len(entry["text"]) + len(_label(entry)) + 2
        if size <= left:
            left -= size
            chosen.append(entry)
    return [{"id": e["id"], "round": e["round"], "kind": e["kind"], "text": e["text"], "label": _label(e)}
            for e in sorted(chosen, key=lambda e: e["id"])]


# ---------------------------------------------------------------------------
# recap
# ---------------------------------------------------------------------------


class RecapConfig(BaseModel):
    """A periodic "story so far" of a long record."""

    model_config = ConfigDict(extra="forbid")

    record: str = Field(..., description="The record to summarise.")
    every: int = Field(..., ge=1, description="Write a recap every N rounds.")
    host: str = Field("writer", description="Host writer name.")
    model: str | None = Field(None, description=MODEL_HINT)
    prompt: str = Field("Summarise the story so far for participants who need to catch up: who did what, what was "
                        "decided, what is still open. Be faithful and brief.", description="What the recap asks for.")
    last: int = Field(50, ge=1, le=500, description="Most new entries one recap reads.")
    visible: str = Field("all", description="Who reads recaps: 'all' or an expression over $viewer and $it.")
    max_chars: int = Field(1500, ge=100, le=10_000, description="Longest recap kept.")
    fallback: Literal["extract"] | None = Field(None,
                                                description="Without a writer: quote the latest entries (default: stop "
                                                            "with an error).")


@mode("host", "recap", RecapConfig,
           "A \"story so far\" of a long record every N rounds, written by a host writer from the entries since "
           "the last recap and posted to the record <name> (delivered as news, recorded for replay).",
           example={"record": "board", "every": 3, "fallback": "extract"})
def _expand_recap(name: str, config: RecapConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    records = contract.get("records") or {}
    source = records.get(config.record)
    if not isinstance(source, Mapping):
        raise MechanismError(f"'{config.record}' is not a declared record", f"records: {', '.join(records) or 'none'}",
                             "record")
    if name in records:
        raise MechanismError(f"a record named '{name}' already exists; the recap posts there", "rename the recap")
    if source.get("visible", "all") != "all" and config.visible == "all":
        raise MechanismError(f"record '{config.record}' is not visible to everyone, so a recap would reveal it",
                             "set `visible` on the recap", "visible")
    return {
        "world": {"host_tape": tape_prop(), f"{name}_cursor": {"type": "int", "default": 0}},
        "records": {name: {"fields": {"text": "text", "through": "int"}, "show": "Story so far: {text}",
                           "visible": config.visible, "description": f"Recaps of {config.record}."}},
        "events": [{"name": f"{name}_recap", "phase": "end", "when": f"$round % {config.every} == 0",
                    "do": [{"host": name, "action": "write"}]}],
    }


@family_action("host", ("recap",), "write",
               example='{"host": "story", "action": "write"}  (recap the new entries of the record now; generated '
                       'every N rounds)')
def _recap_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["host"]
    config = config_of(world, name, RECAP, RecapConfig, where)
    cursor = int(world.props.get(f"{name}_cursor") or 0)
    fields = world.contract.records[config.record].fields
    new = [e for e in world.records(config.record) if e["seq"] > cursor and e.get("to") is None]
    if not new:
        return
    entries = []
    for e in new[-config.last:]:
        author = world.entities.get(e.get("author"))
        text = " · ".join(str(plain(e[f])) for f in fields if e.get(f) is not None)
        entries.append({"round": e["round"], "author": author.name if author else None, "text": text})
    previous = world.records(name)
    request = plain({"task": "recap", "model": config.model, "prompt": config.prompt,
                     "previous": previous[-1]["text"] if previous else "", "entries": entries})
    through = new[-1]["seq"]
    text = consult(world, service=config.host, method="write", site=f"mechanisms.{name}", actor=None,
                   identity={"from": cursor, "through": through}, ask=partial(_ask_write, request),
                   validate=partial(_text, limit=config.max_chars),
                   fallback=partial(_extract, entries, config.max_chars) if config.fallback == "extract" else None)
    world.post(name, {"text": Untrusted(text), "through": through}, None, None, where)
    world.set_world(f"{name}_cursor", through)


def _extract(entries: list[dict[str, Any]], limit: int) -> str:
    lines = [f"Round {e['round']}, {e['author'] or 'the world'}: {e['text']}" for e in entries[-5:]]
    return clip(" / ".join(lines), limit)
