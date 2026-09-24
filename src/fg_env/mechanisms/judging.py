"""Judgment the engine cannot compute, answered by a host: rubric judges and game masters.

``host.judge`` scores text — an action's words, a record entry, with a transcript window — against a
weighted rubric, with one evaluator or a panel, optionally blind; scores, total and rationale
are recorded as typed state the moment they are produced. ``host.game_master`` gives agents a
free-text ``attempt`` tool whose resolution a host proposes and the engine validates against
the contract's allow-list before applying it atomically.
"""
from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..assets.delivery import Attachment, attached_ids, entry_assets
from ..assets.multimodal import host_attachments
from ..contract.base import tape_prop
from ..errors import RunError
from ..expr import Call, ExprError, Untrusted, function
from ..expr.objects import Entity
from ..expr.template import format_value
from ..host import allowlist
from ..host.common import MODEL_HINT, NAME, clip, prop_of, type_list
from ..host.hosts import hosts_for
from ..host.protocols import HostError
from ..host.tape import TAPE, consult, plain
from ..registry import MechanismError, family_action, mechanism_config, mode
from ..world.live import Abort, _plain
from ._common import stage_event

__all__ = ["JudgeConfig", "GameMasterConfig", "total_score"]

RATIONALE_MAX = 2000
JUDGE = "host.judge"
GAME_MASTER = "host.game_master"


# ---------------------------------------------------------------------------
# judge
# ---------------------------------------------------------------------------


@function("host_bound(name)", "Whether the host `name` answers this run: bound live, or its answers are on the run's "
          "tape (a replay, or a restored run). Branch on it to use a host's judgment only when there is one, e.g. a "
          "judge's reading of a speech, and a coded stand-in otherwise.", min_args=1, max_args=1, family="host")
def _host_bound_function(call: Call) -> bool:
    world: Any = call.scope.world
    name = call.arg(0)
    if not isinstance(name, str):
        raise ExprError(f"$host_bound: name must be a host's name, got {format_value(name)}", call.source)
    hosts = hosts_for(world)
    if hosts is not None and hosts.adapter(name) is not None:
        return True
    tape = world.props.get(TAPE)
    recorded = [*(tape.values() if isinstance(tape, Mapping) else ()), *(hosts.replay.values() if hosts else ())]
    return any(isinstance(e, Mapping) and e.get("service") == name and not e.get("fallback") for e in recorded)


class Criterion(BaseModel):
    """One rubric criterion."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field("", description="What this criterion rewards.")
    weight: float = Field(1.0, gt=0, description="Relative weight in the total.")
    scale: tuple[float, float] = Field((1, 10), description="[lowest, highest] score.")

    @model_validator(mode="after")
    def _ordered(self) -> Criterion:
        if not self.scale[0] < self.scale[1]:
            raise ValueError("scale is [low, high] with low below high")
        return self


class Seat(BaseModel):
    """One judge of a panel."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="The judge's name (it is asked as this judge).")
    host: str | None = Field(None, description="Host answering for this judge (default: the mechanism's host).")
    model: str | None = Field(None, description=MODEL_HINT)


class JudgeConfig(BaseModel):
    """A rubric judge scoring text."""

    model_config = ConfigDict(extra="forbid")

    criteria: dict[str, Criterion] = Field(..., min_length=1, description="{criterion: {description, weight, scale}}.")
    instructions: str = Field("", description="What the judge is judging and how (plain text).")
    host: str = Field("judge", description="Host evaluator name.")
    model: str | None = Field(None, description=MODEL_HINT)
    panel: list[Seat] = Field(default_factory=list, description="Several judges; scores are aggregated per criterion.")
    aggregate: Literal["mean", "median", "trimmed_mean"] = Field("mean", description="How a panel's scores combine "
                                                                                     "(trimmed_mean drops the highest "
                                                                                     "and lowest with 3+ judges).")
    out_of: float = Field(10, gt=0, description="The total is the weighted rubric score on a 0..out_of scale.")
    who: str | None = Field(None, description="Type of the entities judged (for `into` and blind aliases).")
    into: str | None = Field(None, description="Number property on `who` that accumulates each total.")
    record: str | None = Field(None, description="Judge every new entry of this record automatically.")
    field: str = Field("text", description="The judged field of `record` entries.")
    stage: str | None = Field(None,
                              description="Judge new `record` entries at the end of this stage (default: at the end of "
                                          "every round).")
    context_last: int = Field(0, ge=0, le=50, description="Earlier `record` entries shown to the judge as context.")
    blind: bool = Field(False, description="The judge sees 'Participant A/B/…' instead of names.")
    visible: str = Field("all", description="Who reads the scores: 'all' or an expression over $viewer and $it.")
    notify: bool = Field(True, description="Deliver scores to agents as news.")
    fallback: Literal["midpoint"] | None = Field(None,
                                                 description="Without an evaluator: score every criterion at its "
                                                             "midpoint, so every entry ties; the run's diagnostics "
                                                             "report it (default: stop with an error).")


@dataclass
class _Item:
    text: str
    subject: Entity | None
    target: int | None = None
    context: list[dict[str, str]] = field(default_factory=list)
    #: Assets the judge receives with the text (`attach`, or a judged entry's files).
    assets: list[str] = field(default_factory=list)


@mode("host", "judge", JudgeConfig,
           "A rubric judge answered by a host evaluator: the `judge` action (`text` and `subject`) in any "
           "effect list (or every new entry of `record`) scores the text per criterion, alone or as a panel, "
           "optionally blind. Each verdict is posted to the record <name> (subject, scores, total, rationale "
           "«quoted», stand_in: true when the midpoint fallback scored it, not an evaluator) and added to "
           "$world.<name>_totals and the `into` property, recorded for replay.",
           example={"record": "speeches", "who": "debater", "into": "score",
                    "criteria": {"logic": {"weight": 2}, "evidence": {"scale": [1, 5]}},
                    "instructions": "Judge each debate speech on its merits."})
def _expand_judge(name: str, config: JudgeConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    records = contract.get("records") or {}
    if config.who is not None:
        type_list(contract, config.who, "who")
    if config.into is not None and (config.who is None or not NAME.match(config.into)):
        raise MechanismError("`into` names a property of the `who` type", "set `who` to the judged type", "into")
    if name in records:
        raise MechanismError(f"a record named '{name}' already exists; the judge posts its verdicts there",
                             "rename the judge or the record")
    fragment: dict[str, Any] = {
        "world": {"host_tape": tape_prop(), f"{name}_totals": {"type": "map", "default": {}}},
        "records": {name: {
            "fields": {"subject": "text", "name": "text", "target": "int", "scores": "map", "total": "number",
                       "rationale": "text", "judges": "list", "stand_in": "bool"},
            "show": "Judged {name}: {total|1}/" + f"{config.out_of:g}" + " — {rationale}",
            "visible": config.visible, "notify": config.notify, "description": f"Verdicts of the judge '{name}'."}},
    }
    if config.into is not None:
        fragment["types"] = {config.who: {"props": {config.into: {"type": "number", "default": 0}}}}
    if config.record is None:
        if config.stage is not None:
            raise MechanismError("`stage` judges new entries of `record`, and no record is set", "set `record`",
                                 "stage")
        return fragment
    source = records.get(config.record)
    if not isinstance(source, Mapping):
        raise MechanismError(f"'{config.record}' is not a declared record", f"records: {', '.join(records) or 'none'}",
                             "record")
    if config.field not in (source.get("fields") or {"text": "text"}):
        raise MechanismError(f"record '{config.record}' has no field '{config.field}'", None, "field")
    fragment["world"][f"{name}_cursor"] = {"type": "int", "default": 0}
    if config.stage is not None:
        fragment["stage_hooks"] = {config.stage: {}}  # it must be a stage there is
        fragment["events"] = [stage_event(config.stage, "end", [{"host": name, "action": "judge"}])]
    else:
        fragment["events"] = [{"name": f"{name}_judging", "phase": "end", "do": [{"host": name, "action": "judge"}]}]
    return fragment


@family_action("host", ("judge",), "judge", keys=("text", "subject", "entry", "context", "attach"),
               example='{"host": "speeches", "action": "judge", "text": "$params.text", "subject": "$actor"}  '
                       '(score `text`, or a record `entry`, with the judge; the verdict goes to the record speeches '
                       'and its totals; without either, judge the new entries of its `record`; `attach` gives the '
                       'judge files, and a judged entry brings its own)')
def _judge_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["host"]
    config = mechanism_config(world, name, JUDGE, JudgeConfig, where)
    if "text" in effect and "entry" in effect:
        raise RunError("give `text` or `entry`, not both", where)
    if "text" in effect or "entry" in effect:
        item = _given(runner, effect, vars, config, where)
        if "attach" in effect:
            item.assets += [key for key in attached_ids(world, effect["attach"], world.scope(**vars), f"{where}.attach")
                            if key not in item.assets]
        _judge(world, name, config, item, where)
    elif config.record is not None:
        for item in _unjudged(world, name, config):
            _judge(world, name, config, item, where)
    else:
        raise RunError(f"give `text` or `entry` (the judge '{name}' has no `record` to judge)", where)


def _given(runner: Any, effect: dict[str, Any], vars: dict[str, Any], config: JudgeConfig, where: str) -> _Item:
    world = runner.world
    if "entry" in effect:
        entry = runner.eval(effect["entry"], vars)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("seq"), int):
            raise RunError(f"`entry` must be a record entry, got {format_value(entry)}", where)
        return _entry_item(world, config, entry)
    text = runner.eval(effect["text"], vars)
    if not isinstance(text, str):
        raise RunError(f"`text` must be text, got {format_value(text)}", where)
    subject = None
    if "subject" in effect:
        value = runner.eval(effect["subject"], vars)
        subject = world.entity(value)
        if value is not None and subject is None:
            raise RunError(f"`subject` must be an entity, got {format_value(value)}", where)
    context: list[dict[str, str]] = []
    if "context" in effect:
        value = runner.eval(effect["context"], vars)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, Mapping):
                author = world.entities.get(item.get("author"))
                context.append({"speaker": author.name if author else "", "text": plain(item.get(config.field, ""))})
            elif item is not None:
                context.append({"speaker": "", "text": plain(format_value(item))})
    return _Item(text, subject, None, context)


def _entry_item(world: Any, config: JudgeConfig, entry: Mapping[str, Any]) -> _Item:
    text = entry.get(config.field)
    subject = world.entities.get(entry.get("author"))
    context: list[dict[str, str]] = []
    if config.record is not None and config.context_last:
        earlier = [e for e in world.records(config.record) if e["seq"] < entry["seq"] and e.get("to") is None]
        for e in earlier[-config.context_last:]:
            author = world.entities.get(e.get("author"))
            context.append({"speaker": author.name if author else "", "text": plain(e.get(config.field) or "")})
    files = next((entry_assets(world, record, entry) for record, rows in world.records_store.items()
                  if any(row is entry for row in rows)), [])
    return _Item(text if isinstance(text, str) else format_value(text), subject, entry["seq"], context, files)


def _unjudged(world: Any, name: str, config: JudgeConfig) -> list[_Item]:
    assert config.record is not None
    rows = world.records(config.record)
    cursor = int(world.props.get(f"{name}_cursor") or 0)
    items = [_entry_item(world, config, e) for e in rows
             if e["seq"] > cursor and e.get("to") is None and e.get(config.field)]
    if rows and rows[-1]["seq"] > cursor:
        world.set_world(f"{name}_cursor", rows[-1]["seq"])
    return items


def _judge(world: Any, name: str, config: JudgeConfig, item: _Item, where: str) -> None:
    seats = config.panel or [Seat(name=name)]
    subject_id = item.subject.id if item.subject is not None else None
    aliases = _aliases(world, config) if config.blind else {}

    def hide(text: str) -> str:
        return _anonymize(world, plain(text), aliases) if config.blind else plain(text)

    label = aliases.get(subject_id or "") if config.blind else (item.subject.name if item.subject is not None else None)
    context = [{"speaker": hide(c["speaker"]), "text": hide(c["text"])} for c in item.context]
    criteria = [{"name": key, "description": c.description, "weight": c.weight, "min": c.scale[0], "max": c.scale[1]}
                for key, c in config.criteria.items()]
    text = hide(item.text)
    fallback: Callable[[], dict[str, Any]] | None = (partial(_midpoint, config) if config.fallback == "midpoint"
                                                     else None)
    answers: list[tuple[str, dict[str, Any]]] = []
    files, hashes = _files(world, item.assets)
    for seat in seats:
        request = plain({"judge": seat.name, "model": seat.model or config.model, "instructions": config.instructions,
                         "criteria": criteria, "subject": label, "text": text, "context": context,
                         "blind": config.blind, "out_of": config.out_of, **files})
        answer = consult(world, service=seat.host or config.host, method="judge", site=f"mechanisms.{name}",
                         actor=subject_id, identity={"seat": seat.name, "target": item.target, "text": text,
                                                     "context": context, **hashes},
                         ask=partial(_ask_judge, request), validate=partial(_verdict, config=config), fallback=fallback)
        answers.append((seat.name, answer))
    scores = {key: _aggregate([a["scores"][key] for _, a in answers], config.aggregate) for key in config.criteria}
    total = total_score(scores, config)
    rationale = Untrusted(answers[0][1]["rationale"] if len(answers) == 1
                          else " | ".join(f"{seat}: {a['rationale']}" for seat, a in answers))
    world.post(name, {"subject": subject_id, "name": item.subject.name if item.subject is not None else None,
                      "target": item.target, "scores": scores, "total": total, "rationale": rationale,
                      "judges": [seat for seat, _ in answers],
                      "stand_in": any(bool(a.get("stand_in")) for _, a in answers)}, None, None, where)
    if item.subject is not None:
        totals = dict(world.props.get(f"{name}_totals") or {})
        totals[item.subject.id] = round(float(totals.get(item.subject.id, 0)) + total, 6)
        world.set_world(f"{name}_totals", totals)
        if config.into is not None:
            world.set_prop(item.subject, config.into, round(prop_of(item.subject, config.into, 0) + total, 6))


def _files(world: Any, ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A host request's `attachments` and the call identity's file hashes (both empty without files)."""
    assets = world.assets.of(ids)
    if not assets:
        return {}, {}
    return ({"attachments": host_attachments([Attachment(asset, world.assets) for asset in assets])},
            {"assets": [asset.hash for asset in assets]})


def _ask_judge(request: dict[str, Any], adapter: Any) -> Any:
    return adapter.judge(request)


def total_score(scores: Mapping[str, float], config: JudgeConfig) -> float:
    """The weighted rubric score on a 0..out_of scale."""
    weight = sum(c.weight for c in config.criteria.values())
    earned = sum(c.weight * (scores[key] - c.scale[0]) / (c.scale[1] - c.scale[0])
                 for key, c in config.criteria.items())
    return round(config.out_of * earned / weight, 4)


def _verdict(answer: Any, config: JudgeConfig) -> dict[str, Any]:
    if not isinstance(answer, Mapping):
        raise HostError("a verdict is an object {scores, rationale}")
    extra = sorted(set(answer) - {"scores", "rationale"})
    if extra:
        raise HostError(f"a verdict has only scores and rationale, got {extra}")
    scores = answer.get("scores")
    if not isinstance(scores, Mapping):
        raise HostError("scores must be an object {criterion: number}")
    unknown = sorted(set(scores) - set(config.criteria))
    missing = [key for key in config.criteria if key not in scores]
    if unknown or missing:
        raise HostError(f"scores must cover exactly {list(config.criteria)} (missing {missing}, unknown {unknown})")
    for key, criterion in config.criteria.items():
        value = scores[key]
        low, high = criterion.scale
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise HostError(f"the score for '{key}' must be a number from {low:g} to {high:g}, got {value!r}")
    rationale = answer.get("rationale", "")
    if not isinstance(rationale, str):
        raise HostError("rationale must be text")
    return {"scores": {key: scores[key] for key in config.criteria},
            "rationale": clip(rationale.strip(), RATIONALE_MAX)}


def _midpoint(config: JudgeConfig) -> dict[str, Any]:
    return {"scores": {key: (c.scale[0] + c.scale[1]) / 2 for key, c in config.criteria.items()},
            "rationale": "No evaluator was available; every criterion was scored at its midpoint.",
            "stand_in": True}  # a live verdict never carries it: validation allows only scores and rationale


def _aggregate(values: list[float], how: str) -> float:
    if how == "median":
        result = statistics.median(values)
    elif how == "trimmed_mean" and len(values) >= 3:
        result = statistics.fmean(sorted(values)[1:-1])
    else:
        result = statistics.fmean(values)
    return round(float(result), 6)


def _aliases(world: Any, config: JudgeConfig) -> dict[str, str]:
    if config.who is not None:
        pool = [e for e in world.entities.values() if world.is_a(e.entity_type, config.who)]
    else:
        pool = [e for e in world.entities.values() if world.contract.is_agent(e.entity_type)]
    return {e.id: f"Participant {_letters(i)}" for i, e in enumerate(pool)}


def _letters(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        out = chr(65 + rest) + out
    return out


def _anonymize(world: Any, text: str, aliases: Mapping[str, str]) -> str:
    names: list[tuple[str, str]] = []
    for entity_id, alias in aliases.items():
        entity = world.entities[entity_id]
        names += [(entity.name, alias), (entity_id, alias)]
    for word, alias in sorted({n for n in names if n[0]}, key=lambda n: -len(n[0])):
        text = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", alias, text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# game_master
# ---------------------------------------------------------------------------


class AllowRule(BaseModel):
    """One kind of change a game master may make."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    effect: Literal["set", "set_world", "transfer", "move", "news"] = Field(..., description="The change it allows.")
    target: str = Field("actor",
                        description="set/move: 'actor' or an expression over $actor giving the entities it may touch.")
    prop: str | None = Field(None, description="set/set_world/transfer: the property.")
    min: float | None = Field(None, description="Lowest value a number may become.")
    max: float | None = Field(None, description="Highest value a number may become.")
    delta: float | None = Field(None, ge=0, description="Largest change of a number in one attempt.")
    values: list[Any] | None = Field(None, description="The only values it may set.")
    max_chars: int = Field(200, ge=1, le=4000, description="Longest text it may set or spread as news.")
    giver: str = Field("actor", alias="from", description="transfer: 'actor' or an expression giving who may give.")
    to: str | None = Field(None,
                           description="transfer: expression giving who may receive ('actor' works); move: 'adjacent' "
                                       "or an expression over $actor and $it giving places.")
    amount: float | None = Field(None, gt=0, description="transfer: most that may move from one giver in one attempt.")
    description: str = Field("", description="Shown to the game master.")

    @model_validator(mode="after")
    def _complete(self) -> AllowRule:
        needs = {"set": ("prop",), "set_world": ("prop",), "transfer": ("prop", "to", "amount"), "move": ("to",),
                 "news": ()}[self.effect]
        missing = [key for key in needs if getattr(self, key) is None]
        if missing:
            raise ValueError(f"`{self.effect}` needs {', '.join(missing)}")
        if self.prop is not None and not NAME.match(self.prop):
            raise ValueError(f"prop must be a property name, got {self.prop!r}")
        return self


class GameMasterConfig(BaseModel):
    """Free-text attempts resolved by a host within an allow-list."""

    model_config = ConfigDict(extra="forbid")

    who: str | list[str] = Field(..., description="Agent type(s) that may attempt things.")
    allow: list[AllowRule] = Field(..., min_length=1, description="Every change the game master may make.")
    host: str = Field("game_master", description="Host game master name.")
    model: str | None = Field(None, description=MODEL_HINT)
    tool: str = Field("attempt", description="Name of the free-text tool.")
    description: str = Field("", description="Tool description (default explains the tool).")
    max_chars: int = Field(500, ge=1, le=4000, description="Longest attempt text, in characters.")
    rules: str = Field("", description="How the world works, for the game master (plain text).")
    context: dict[str, str] = Field(default_factory=dict,
                                    description="{name: expression over $actor} values shown to the game master.")
    max_effects: int = Field(4, ge=1, le=20, description="Most changes one attempt may cause.")
    per_turn: int | None = Field(1, ge=1, description="Attempts per turn.")
    terminal: bool = Field(True, description="An attempt ends the turn.")
    visible: str = Field("all", description="Who reads the attempt log: 'all' or an expression over $viewer and $it.")
    fallback: Literal["refuse"] | None = Field(None,
                                               description="Without a host: refuse every attempt (default: stop with "
                                                           "an error).")


@mode("host", "game_master", GameMasterConfig,
           "Free-text attempts resolved by a host game master: agents get an `attempt(text)` tool; the host "
           "proposes effects and the engine applies them only when every one fits `allow` (kinds, targets, "
           "properties, bounds, amounts, destinations) — atomically, or refuses with the reason. Attempts, "
           "narration and changes go to the record <name>; the actor is told the result; answers are recorded "
           "for replay.",
           example={"who": "adventurer", "rules": "A small tavern. Be fair and terse.",
                    "allow": [{"effect": "set", "prop": "health", "min": 0, "max": 10, "delta": 3},
                              {"effect": "transfer", "prop": "gold", "to": "$filter(adventurer, $it.id != $actor.id)",
                               "amount": 5},
                              {"effect": "move", "to": "adjacent"}, {"effect": "news", "max_chars": 160}]})
def _expand_game_master(name: str, config: GameMasterConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    by = type_list(contract, config.who, "who")
    if not NAME.match(config.tool):
        raise MechanismError(f"tool must be a tool name, got {config.tool!r}", None, "tool")
    types = contract.get("types") or {}
    world = contract.get("world") or {}
    for index, rule in enumerate(config.allow):
        if rule.effect == "set_world" and rule.prop not in world:
            raise MechanismError(f"the world has no property '{rule.prop}'", f"world: {', '.join(world) or 'none'}",
                                 f"allow[{index}].prop")
        if rule.effect in ("set", "transfer") and not any(rule.prop in (t.get("props") or {}) for t in types.values()
                                                          if isinstance(t, Mapping)):
            raise MechanismError(f"no type declares the property '{rule.prop}'", None, f"allow[{index}].prop")
        if rule.effect == "move" and rule.to == "adjacent" and not contract.get("space"):
            raise MechanismError("`adjacent` needs a graph or grid space", None, f"allow[{index}].to")
    description = config.description or ("Try something, described in your own words. The game master decides "
                                         "what happens, within the rules of this world.")
    action: dict[str, Any] = {
        "by": by if len(by) > 1 else by[0], "description": description, "terminal": config.terminal,
        "params": {"text": {"type": "text", "max_len": config.max_chars, "description": "What you try to do."}},
        "do": [{"host": name, "action": "resolve", "text": "$params.text"}], "outcome": f"{{$actor.{name}_told}}",
    }
    if config.per_turn is not None:
        action["per_turn"] = config.per_turn
    return {
        "types": {t: {"props": {f"{name}_told": {"type": "text", "default": "", "private": True}}} for t in by},
        "world": {"host_tape": tape_prop()},
        "actions": {config.tool: action},
        "records": {name: {
            "fields": {"attempt": "text", "narration": "text", "changes": "list", "refused": "bool", "reason": "text"},
            "show": "{author} tried {attempt} → {$it.reason if $it.refused else $it.narration}",
            "visible": config.visible, "description": f"Attempts resolved by the game master '{name}'."}},
    }


def _absent() -> dict[str, Any]:
    return {"refuse": "No game master is present."}


@family_action("host", ("game_master",), "resolve", keys=("text", "attach"), required=("text",),
               example='{"host": "gm", "action": "resolve", "text": "$params.text"}  (the game master resolves the '
                       'actor\'s attempt; changes apply only within its allow-list, and $actor.gm_told says what '
                       'happened; `attach` gives it files)')
def _resolve_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["host"]
    config = mechanism_config(world, name, GAME_MASTER, GameMasterConfig, where)
    actor = vars.get("actor")
    if not isinstance(actor, Entity):
        raise RunError("`resolve` runs inside an action (it needs $actor)", where)
    text = runner.eval(effect["text"], vars)
    if not isinstance(text, str):
        raise RunError(f"`text` must be text, got {format_value(text)}", where)
    rules = allowlist.resolve_rules(runner, config.allow, vars, f"mechanisms.{name}")
    context = {key: _plain(runner.eval(expr, vars)) for key, expr in config.context.items()}
    attached = (attached_ids(world, effect["attach"], world.scope(**vars), f"{where}.attach") if "attach" in effect
                else [])
    files, hashes = _files(world, attached)
    request = plain({"game_master": name, "model": config.model, "rules": config.rules,
                     "actor": {"id": actor.id, "name": actor.name, "type": actor.entity_type,
                               "props": _plain(dict(actor.properties))},
                     "attempt": text, "context": context, "allowed": allowlist.describe(rules),
                     "max_effects": config.max_effects, "time": world.clock_label(), **files})
    proposal = consult(world, service=config.host, method="resolve", site=f"mechanisms.{name}", actor=actor.id,
                       identity={"attempt": text, **hashes}, ask=lambda adapter: adapter.resolve(request),
                       fallback=_absent if config.fallback == "refuse" else None)
    plan, refusal = allowlist.validate(world, rules, proposal, config.max_effects)
    if refusal is None:
        mark = world.journal.mark()
        try:
            allowlist.apply(runner, plan, actor, name, where)
        except Abort as abort:
            world.journal.rollback(mark)
            refusal = abort.reason
    changes = [change.summary for change in plan.changes] if refusal is None else []
    narration = Untrusted(plan.narration) if plan.narration and refusal is None else None
    if refusal is not None:
        told = ("The game master did not allow that: "
                f"{format_value(refusal) if isinstance(refusal, Untrusted) else refusal}")
    else:
        told = f"The game master: {format_value(narration)}" if narration else "The game master lets it happen."
        if changes:
            told += " Changes: " + "; ".join(changes) + "."
    world.post(name, {"attempt": text, "narration": narration, "changes": changes, "refused": refusal is not None,
                      "reason": refusal}, actor.id, None, where)
    world.set_prop(actor, f"{name}_told", told)

