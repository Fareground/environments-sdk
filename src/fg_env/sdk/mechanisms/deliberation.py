"""Deliberation: discussion until everyone is ready, floor control, and Robert's-rules-lite motions.

.. code-block:: json

    "mechanisms": {"hall": {"kind": "decision", "mode": "deliberation", "who": "resident", "chair": "moderator",
                            "floor": true, "speaker_limit": 2, "question": "Should the town build a skate park?"}}

Each round a sequential discussion stage (``<name>``) repeats passes until every member is ready
or the pass cap (the backstop) is reached; agents with nothing new are skipped. Anything said — a
speech, motion, amendment, second or withdrawal — clears every member's readiness, because each
has something new to read; the discussion is over once every member is ready, the last speaker
aside (a speaker whose words drew no reply hears the discussion close, as in the Fareground
Council). An agent that ends its turn without acting becomes ready (unless
``ready_when_silent`` is false). With a chair and ``floor``, members raise hands and speak only
once recognized, for at most ``speaker_limit`` speeches.

Motions: ``propose`` (needs a ``second`` unless ``second`` is false), ``amend`` the open motion
(an amendment is voted first), ``withdraw``, and ``call_question`` — by the chair when there is
one, else by any member once the motion has been debated ``min_debate`` speeches. The discussion
also puts an open question to a vote when everyone is ready (``vote_when_ready``). Votes run in a
simultaneous stage ``<name>_vote`` and are counted with the ballot mode's :func:`tally`.

State: the world prop ``<name>`` ({phase, stack, floor, hands, floor_used, ballots, decisions,
next, last}) and each member's ``<name>_ready``. Speeches, motions and seconds are entries of the
public record ``<name>``; results are announced as ``<name>`` events.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, function
from ..registry import MechanismError, family_action, mode
from ..template import format_value
from ..world import Abort
from ._common import ToolsSetting, tools_field
from ._social import props, check_expr, config_of, entity, only_use, require_type, single_use_check
from .voting import tally

__all__ = ["DeliberationConfig"]

KIND = "decision.deliberation"
_RECENT_DECISIONS = 3
#: Floor-control refusals, the same wherever they are given (a tool's requirement or the effect itself).
_NO_FLOOR = "You do not hold the floor: raise your hand and wait to be recognized."
_RAISED = "Your hand is raised: wait to be recognized."
_HOLDING = "You already hold the floor: speak, or yield it."


class DeliberationConfig(BaseModel):
    """A deliberating body: members, an optional chair, motions and votes."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that deliberates and votes.")
    chair: Optional[str] = Field(None, description="Agent type of the chair: recognizes speakers and calls the question.")
    question: str = Field("", description="What the body is deliberating, shown every turn.")
    floor: bool = Field(False, description="Floor control (needs a chair): members raise hands and speak only when recognized.")
    speaker_limit: Optional[int] = Field(2, ge=1, description="Speeches per recognition before the floor returns to the chair.")
    passes: int = Field(6, ge=1, le=100, description="Most passes of discussion per round (the backstop).")
    max_chars: int = Field(600, ge=1, le=4000, description="Longest speech or motion, in characters.")
    motions: bool = Field(True, description="Members may propose motions.")
    second: bool = Field(True, description="A motion or amendment needs a second before debate.")
    amendments: bool = Field(True, description="Members may amend the open motion.")
    min_debate: int = Field(1, ge=0, description="Speeches on a question before a member may call it (no chair).")
    method: Literal["majority", "supermajority"] = Field("majority", description="majority (more than half of votes cast) | supermajority (threshold, default 2/3).")
    threshold: Optional[float] = Field(None, ge=0, le=1, description="Share of votes needed to pass.")
    quorum: Optional[float] = Field(None, ge=0, le=1, description="Share of members who must vote (abstentions count).")
    private: bool = Field(False, description="Votes stay secret and only the result is announced; otherwise each vote is announced.")
    ready_when_silent: bool = Field(True, description="Ending a discussion turn without acting marks the member ready.")
    vote_when_ready: bool = Field(True, description="When every member is ready, an open question goes to a vote.")
    backstop: Literal["adjourn", "vote"] = Field("adjourn", description="When the pass cap is hit: adjourn to the next round, or vote on the open question.")
    end: Literal["decision", "adoption", "never"] = Field("decision", description="End the run once a main motion is decided, only once one passes, or never.")
    when: Optional[str] = Field(None, description="Hold the discussion only when true (e.g. \"$round <= 5\").")
    tools: ToolsSetting = tools_field()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _fresh() -> Dict[str, Any]:
    return {"phase": "debate", "stack": [], "floor": None, "hands": [], "floor_used": 0, "ballots": {},
            "decisions": [], "next": 1, "last": None}


def _state(world: Any, name: str) -> Dict[str, Any]:
    raw = world.props.get(name) or _fresh()
    return {**raw, "stack": [dict(m) for m in raw["stack"]], "hands": list(raw["hands"]),
            "ballots": dict(raw["ballots"]), "decisions": list(raw["decisions"])}


def _members(world: Any, config: DeliberationConfig) -> List[Entity]:
    return list(world.entities_of(config.who))


def _all_ready(world: Any, name: str, config: DeliberationConfig, state: Optional[Mapping[str, Any]] = None) -> bool:
    """Every member is ready, except perhaps the last speaker: nothing was said after them."""
    last = (state if state is not None else (world.props.get(name) or _fresh())).get("last")
    return all(props(m).get(f"{name}_ready") or m.id == last for m in _members(world, config))


def _describe(world: Any, item: Mapping[str, Any]) -> str:
    """A motion or amendment by number: numbers name them in text, and no tool takes one (so no [id] handle)."""
    what = f"motion {item['id']}" if item["kind"] == "motion" else f"amendment {item['id']} to motion {item['target']}"
    return f"{what} {format_value(item['text'])}"


def _person(world: Any, entity_id: Optional[str]) -> str:
    """A member as the reader sees entities: by name, with the [id] handle it can inspect them by."""
    found = world.entities.get(entity_id) if entity_id else None
    return format_value(found) if found is not None else "—"


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _use(call: Call) -> Any:
    world: Any = call.scope.world
    name = only_use(world, KIND, call.source)
    return world, name, config_of(world, name, KIND, DeliberationConfig)


@function("pending_motion()", "The question before the body (the top motion or amendment) as {id, kind, text, mover, "
          "seconder, status, speeches, target}, or null (deliberation mechanism).", min_args=0, max_args=0)
def _pending_fn(call: Call) -> Optional[Dict[str, Any]]:
    world, name, _ = _use(call)
    stack = (world.props.get(name) or _fresh())["stack"]
    return dict(stack[-1]) if stack else None


@function("discussion_over()", "True when the discussion should stop this round: a vote is due, or every member (the last "
          "speaker aside) is ready.",
          min_args=0, max_args=0)
def _over_fn(call: Call) -> bool:
    world, name, config = _use(call)
    return bool((world.props.get(name) or _fresh())["phase"] == "voting" or _all_ready(world, name, config))


@function("decisions()", "Decided main motions, oldest first: [{id, text, passed, counts, round}].", min_args=0, max_args=0)
def _decisions_fn(call: Call) -> List[Dict[str, Any]]:
    world, name, _ = _use(call)
    return [dict(d) for d in (world.props.get(name) or _fresh())["decisions"]]


@function("house(viewer)", "The state of the deliberation as the viewer should read it: question, floor, hands, readiness.",
          min_args=1, max_args=1)
def _house_fn(call: Call) -> str:
    world, name, config = _use(call)
    viewer = world.entity(call.arg(0).id if isinstance(call.arg(0), Entity) else call.arg(0))
    return _house(world, name, config, viewer)


def _house(world: Any, name: str, config: DeliberationConfig, viewer: Optional[Entity]) -> str:
    state = world.props.get(name) or _fresh()
    lines: List[str] = []
    if config.question:
        lines.append(f"Question: {config.question}")
    stack = state["stack"]
    if not stack:
        lines.append("No motion is before the body." + (" Any member may propose one." if config.motions else ""))
    for item in stack:
        second = f", seconded by {_person(world, item['seconder'])}" if item["seconder"] else (
            " — needs a second" if item["status"] == "proposed" else "")
        lines.append(f"Before the body: {_describe(world, item)} (moved by {_person(world, item['mover'])}{second}; "
                     f"{item['speeches']} speeches)")
    if config.floor:
        holder = state["floor"]
        used = f" ({state['floor_used']} of {config.speaker_limit} speeches)" if holder and config.speaker_limit else ""
        hands = ", ".join(_person(world, h) for h in state["hands"]) or "none"
        lines.append(f"Floor: {_person(world, holder) if holder else 'open (the chair recognizes speakers)'}{used} · "
                     f"Hands raised: {hands}")
    if state["phase"] == "voting" and world.stage == f"{name}_vote":
        lines.append(f"Voting now: {len(state['ballots'])} of {len(_members(world, config))} ballots cast.")
    elif state["phase"] == "voting":
        lines.append("The question has been called: voting opens in the next stage.")
    else:
        members = _members(world, config)
        ready = sum(1 for m in members if props(m).get(f"{name}_ready"))
        mine = ""
        if viewer is not None and world.is_a(viewer.entity_type, config.who):
            mine = " (you: ready)" if props(viewer).get(f"{name}_ready") else " (you: not ready)"
        lines.append(f"Ready to conclude: {ready} of {len(members)}{mine}")
    for decision in state["decisions"][-_RECENT_DECISIONS:]:
        verdict = "passed" if decision["passed"] else "failed"
        counts = ", ".join(f"{k} {v}" for k, v in decision["counts"].items())
        lines.append(f"Decided: motion {decision['id']} {format_value(decision['text'])} — {verdict} ({counts})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The decision op's deliberation actions
# ---------------------------------------------------------------------------

#: action → (its keys, all required; generated by the mechanism itself; example keys; what it does). The actor is $actor.
_ACTIONS: Dict[str, Tuple[Tuple[str, ...], bool, str, str]] = {
    "speak": (("text",), False, '"text": "$params.text"', "a speech; under floor control only by the member holding the floor"),
    "ready": ((), False, "", "nothing more to add this round"),
    "raise_hand": ((), False, "", "ask the chair for the floor"),
    "recognize": (("who",), False, '"who": "$params.who"', "the chair gives the floor to a member whose hand is raised"),
    "yield": ((), False, "", "give the floor back to the chair"),
    "propose": (("text",), False, '"text": "$params.text"', "move a motion"),
    "second": ((), False, "", "second the proposal waiting for a second"),
    "amend": (("text",), False, '"text": "$params.text"', "new wording for the open motion, voted on before it"),
    "withdraw": ((), False, "", "withdraw your pending motion or amendment"),
    "call_question": ((), False, "", "end debate and put the open question to a vote"),
    "vote": (("choice",), False, '"choice": "$params.choice"', "yes, no or abstain on the question put"),
    "open": ((), True, "", "start a round's discussion"),
    "close": ((), True, "", "end the discussion, putting a ready question to a vote"),
    "idle": ((), True, "", "the actor ended a discussion turn without acting"),
    "tally": ((), True, "", "count the vote"),
}


def _runner(action: str) -> Callable[[Any, Dict[str, Any], Dict[str, Any], str], None]:
    def run(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["decision"]
        config = config_of(world, name, KIND, DeliberationConfig)
        state = _state(world, name)
        if action == "open":
            _open(world, name, config, state)
        elif action == "close":
            _close(world, name, config, state)
        elif action == "tally":
            _tally(world, name, config, state)
        else:
            actor = vars.get("actor")
            if not isinstance(actor, Entity):
                raise RunError(f"`{action}` runs inside an action or on_idle ($actor)", where)
            value: Dict[str, Any] = {key: runner.eval(effect[key], vars) for key in ("text", "who", "choice") if key in effect}
            _member_act(world, name, config, state, actor, action, value, where)
        world.set_world(name, state)

    return run


def _register_actions() -> None:
    for action, (keys, internal, fields, doc) in _ACTIONS.items():
        example = '{"decision": "hall", "action": "' + action + '"' + (f", {fields}" if fields else "") + f"}}  ({doc})"
        family_action("decision", ("deliberation",), action, keys=keys, required=keys, internal=internal,
                      example=example, was=("deliberate",))(_runner(action))


_register_actions()


def _set_ready(world: Any, name: str, member: Entity, ready: bool) -> None:
    if bool(props(member).get(f"{name}_ready")) != ready:
        world.set_prop(member, f"{name}_ready", ready)


def _open(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any]) -> None:
    for member in _members(world, config):
        _set_ready(world, name, member, False)
    state.update(floor=None, hands=[], floor_used=0, last=None)


def _close(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any]) -> None:
    if state["phase"] == "voting":
        return
    consensus = _all_ready(world, name, config, state)
    for member in _members(world, config):  # forced when the backstop cut the discussion short
        _set_ready(world, name, member, True)
    if not consensus:
        world.emit(name, f"Time is up: discussion closes for this {world.contract.clock.unit}.", data={"mechanism": name})
    top = state["stack"][-1] if state["stack"] else None
    if top is not None and top["status"] == "open" and (
            (consensus and config.vote_when_ready) or (not consensus and config.backstop == "vote")):
        _put_question(world, name, state, top)
    state.update(floor=None, hands=[], floor_used=0, last=None)


def _put_question(world: Any, name: str, state: Dict[str, Any], item: Mapping[str, Any]) -> None:
    state["phase"] = "voting"
    state["ballots"] = {}
    world.emit(name, f"The question is put to a vote: {_describe(world, item)}.", data={"mechanism": name, "motion": item["id"]})


def _member_act(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity, act: str,
                value: Dict[str, Any], where: str) -> None:
    is_chair = config.chair is not None and world.is_a(actor.entity_type, config.chair)
    is_member = world.is_a(actor.entity_type, config.who)
    if act == "idle":
        if is_member and config.ready_when_silent and state["phase"] == "debate":
            _set_ready(world, name, actor, True)
        return
    if act in ("recognize",) and not is_chair:
        raise Abort("Only the chair can do that.")
    if act == "call_question" and config.chair is not None and not is_chair:
        raise Abort("Only the chair can call the question.")
    if act not in ("recognize", "call_question") and not is_member:
        raise Abort(f"{actor.name} is not a member of this body.")
    if act == "vote":
        _vote(state, actor, value.get("choice"))
        return
    if state["phase"] != "debate":
        raise Abort("A vote is under way; wait for the result.")
    top = state["stack"][-1] if state["stack"] else None
    if act == "ready":
        _set_ready(world, name, actor, True)
    elif act == "raise_hand":
        _hand(world, name, config, state, actor)
    elif act == "recognize":
        _recognize(world, name, config, state, entity(world, value.get("who"), where))
    elif act == "yield":
        if state["floor"] != actor.id:
            raise Abort("You do not hold the floor.")
        state.update(floor=None, floor_used=0)
        world.emit(name, f"{actor.name} yields the floor.", actor=actor.id, data={"mechanism": name})
    elif act == "speak":
        _speak(world, name, config, state, actor, value.get("text"), top, where)
    elif act == "propose":
        _propose(world, name, config, state, actor, value.get("text"), "motion", where)
    elif act == "amend":
        if not config.amendments:
            raise Abort("Amendments are not allowed here.")
        if top is None or top["kind"] != "motion" or top["status"] != "open":
            raise Abort("There is no open motion to amend.")
        _propose(world, name, config, state, actor, value.get("text"), "amendment", where)
    elif act == "second":
        _second(world, name, config, state, actor, top, where)
    elif act == "withdraw":
        if top is None or top["mover"] != actor.id:
            raise Abort("You have no pending motion to withdraw.")
        state["stack"].pop()
        _record(world, name, actor, f"withdraws {_describe(world, top)}", "", top["id"], where)
        _spoke(world, name, config, state, actor)
    elif act == "call_question":
        _call(world, name, config, state, actor, top, is_chair, where)


def _hand(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity) -> None:
    if not config.floor or config.chair is None:
        raise Abort("There is no floor control here; just speak.")
    if state["floor"] == actor.id:
        raise Abort(_HOLDING)
    if actor.id in state["hands"]:
        raise Abort(_RAISED)
    state["hands"].append(actor.id)
    chairs = [c.id for c in world.entities_of(config.chair)]
    why = f"{actor.name} raised a hand and asks for the floor."
    world.emit(name, why, actor=actor.id, to=tuple(chairs), data={"mechanism": name, "hand": actor.id})
    for chair in chairs:
        world.request_wake(chair, why)


def _recognize(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], who: Entity) -> None:
    if not config.floor:
        raise Abort("There is no floor control here.")
    if who.id not in state["hands"]:
        raise Abort(f"{who.name} has not raised a hand.")
    state["hands"].remove(who.id)
    state.update(floor=who.id, floor_used=0)
    world.emit(name, f"The chair gives the floor to {who.name}.", data={"mechanism": name, "floor": who.id})
    world.request_wake(who.id, "The chair gave you the floor.")


def _needs_floor(config: DeliberationConfig, state: Dict[str, Any], actor: Entity) -> None:
    if config.floor and state["floor"] != actor.id:
        raise Abort(_RAISED if actor.id in state["hands"] else _NO_FLOOR)


def _text(config: DeliberationConfig, text: Any) -> Any:
    if not isinstance(text, str) or not text.strip():
        raise Abort("Say something.")
    if len(text) > config.max_chars:
        raise Abort(f"At most {config.max_chars} characters; yours has {len(text)}.")
    return text


def _spoke(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity) -> None:
    """Something new was said: every member has it to read, so nobody is ready until they have."""
    for member in _members(world, config):
        _set_ready(world, name, member, False)
    state["last"] = actor.id


def _use_floor(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity) -> None:
    _spoke(world, name, config, state, actor)
    if not config.floor:
        return
    state["floor_used"] += 1
    if config.speaker_limit is not None and state["floor_used"] >= config.speaker_limit:
        state.update(floor=None, floor_used=0)
        world.emit(name, f"{actor.name}'s time is up; the floor returns to the chair.", data={"mechanism": name})


def _speak(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity, text: Any,
           top: Optional[Dict[str, Any]], where: str) -> None:
    _needs_floor(config, state, actor)
    body = _text(config, text)
    if top is not None and top["status"] == "open":
        top["speeches"] += 1
    _record(world, name, actor, "says", body, top["id"] if top else None, where)
    _use_floor(world, name, config, state, actor)


def _propose(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity, text: Any,
             kind: str, where: str) -> None:
    if kind == "motion" and (not config.motions or state["stack"]):
        raise Abort("A motion is already before the body." if state["stack"] else "Motions are not allowed here.")
    _needs_floor(config, state, actor)
    body = _text(config, text)
    target = state["stack"][-1]["id"] if kind == "amendment" else None
    item = {"id": state["next"], "kind": kind, "text": body, "mover": actor.id, "seconder": None,
            "status": "proposed" if config.second else "open", "speeches": 0, "target": target, "round": world.round}
    state["next"] += 1
    state["stack"].append(item)
    says = f"moves motion {item['id']}" if kind == "motion" else f"moves amendment {item['id']} to motion {target}"
    _record(world, name, actor, says + (" (needs a second)" if config.second else ""), body, item["id"], where)
    _use_floor(world, name, config, state, actor)


def _second(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity,
            top: Optional[Dict[str, Any]], where: str) -> None:
    if top is None or top["status"] != "proposed":
        raise Abort("Nothing is waiting for a second.")
    if top["mover"] == actor.id:
        raise Abort("You cannot second your own motion.")
    top.update(status="open", seconder=actor.id)
    _spoke(world, name, config, state, actor)
    _record(world, name, actor, f"seconds {top['kind']} {top['id']}; it is open for debate",
            "", top["id"], where)


def _call(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any], actor: Entity,
          top: Optional[Dict[str, Any]], is_chair: bool, where: str) -> None:
    if top is None or top["status"] != "open":
        raise Abort("There is no open question to call.")
    if not is_chair and top["speeches"] < config.min_debate:
        raise Abort(f"The question needs {config.min_debate} speech(es) of debate first ({top['speeches']} so far).")
    _record(world, name, actor, f"calls the question on {top['kind']} {top['id']}", "", top["id"], where)
    _put_question(world, name, state, top)


def _vote(state: Dict[str, Any], actor: Entity, choice: Any) -> None:
    if state["phase"] != "voting":
        raise Abort("No vote is open.")
    if actor.id in state["ballots"]:
        raise Abort("You have already voted.")
    if choice not in ("yes", "no", "abstain"):
        raise Abort("Vote yes, no or abstain.")
    state["ballots"][actor.id] = choice


def _tally(world: Any, name: str, config: DeliberationConfig, state: Dict[str, Any]) -> None:
    if state["phase"] != "voting" or not state["stack"]:
        state["phase"] = "debate"
        return
    item = state["stack"].pop()
    eligible = len(_members(world, config))
    result = tally(config.method, state["ballots"], ["yes", "no"], config.threshold, "none", world.rng, eligible, config.quorum)
    passed = result["winner"] == "yes"
    counts = {"yes": result["counts"].get("yes", 0), "no": result["counts"].get("no", 0),
              "abstain": result["cast"] - result["votes"]}
    detail = ", ".join(f"{k} {v}" for k, v in counts.items())
    if not config.private and state["ballots"]:
        detail += "; " + ", ".join(f"{_person(world, voter)} {vote}" for voter, vote in state["ballots"].items())
    reason = " (no quorum)" if result.get("reason") == "no quorum" else ""
    verdict = "passes" if passed else "fails"
    state.update(phase="debate", ballots={})
    if item["kind"] == "amendment":
        if passed and state["stack"]:
            state["stack"][-1] = {**state["stack"][-1], "text": item["text"]}
        world.emit(name, f"The {_describe(world, item)} {verdict}{reason} ({detail})." +
                   (" The motion now reads as amended." if passed else ""),
                   data={"mechanism": name, "motion": item["id"], "passed": passed, "counts": counts})
        return
    state["decisions"].append({"id": item["id"], "text": item["text"], "passed": passed, "counts": counts, "round": world.round})
    text = f"Motion {item['id']} {format_value(item['text'])} {verdict}{reason} ({detail})."
    world.emit(name, text, data={"mechanism": name, "motion": item["id"], "passed": passed, "counts": counts})
    if config.end == "decision" or (config.end == "adoption" and passed):
        world.request_end(name, None, text)


def _record(world: Any, name: str, actor: Entity, says: str, text: Any, motion: Optional[int], where: str) -> None:
    world.post(name, {"says": says, "text": text, "motion": motion}, actor.id, None, where)


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


@mode("decision", "deliberation", DeliberationConfig,
           "A deliberating body: a discussion stage `<name>` that repeats passes until every member is ready (or the pass "
           "cap), optional chair with floor control (raise hand, recognize, speaker limits), motions with seconds, "
           "amendments, calling the question, and a vote stage `<name>_vote` counted by majority or supermajority. "
           "Read state with $pending_motion(), $decisions(), $discussion_over(), $house(viewer).",
           example={"who": "resident", "chair": "moderator", "floor": True,
                    "question": "Should the town build a skate park?", "passes": 8}, was="deliberation")
def _expand(name: str, config: DeliberationConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    single_use_check(KIND, contract)
    require_type(contract, config.who, "who", agent=True)
    require_type(contract, config.chair, "chair", agent=True)
    if config.floor and config.chair is None:
        raise MechanismError("floor control needs a chair", "set `chair` to the chair's agent type, or floor: false", "floor")
    if config.chair is not None and config.chair == config.who:
        raise MechanismError("the chair must be its own agent type", "declare e.g. types.moderator", "chair")
    check_expr(config.when, "when", ())
    debate = f"$world.{name}.phase == 'debate'"
    members, chair = config.who, config.chair
    holds = f"$world.{name}.floor == $actor.id"
    raised = f"$actor.id in $world.{name}.hands"
    floor = [{"expr": f"{holds} or {raised}", "why": _NO_FLOOR}, {"expr": holds, "why": _RAISED}] if config.floor else []
    open_debate = [{"expr": debate, "why": "A vote is under way."}]
    text = {"type": "text", "max_len": config.max_chars}

    def act(by: str, description: str, do: Dict[str, Any], when: List[Any], params: Optional[Dict[str, Any]] = None,
            **extra: Any) -> Dict[str, Any]:
        return {"by": by, "description": description, "params": params or {}, "when": when,
                "do": [{"decision": name, **do}], **extra}

    top = "$pending_motion()"
    actions: Dict[str, Any] = {
        f"{name}_speak": act(members, "Speak to the body.", {"action": "speak", "text": "$params.text"}, open_debate + floor,
                             {"text": {**text, "description": "Your speech."}}, outcome="You spoke."),
        f"{name}_ready": act(members, "Say you have nothing more to add this round.", {"action": "ready"},
                             open_debate + [{"expr": f"not $actor.{name}_ready", "why": "You are already ready."}],
                             outcome="You are ready to conclude.", private=True, terminal=True),
    }
    if config.floor:
        actions[f"{name}_raise_hand"] = act(members, "Ask the chair for the floor.", {"action": "raise_hand"}, open_debate + [
            {"expr": f"not ({holds})", "why": _HOLDING}, {"expr": f"not ({raised})", "why": _RAISED}],
            outcome="Your hand is raised.", private=True)
        actions[f"{name}_yield"] = act(members, "Give the floor back to the chair.", {"action": "yield"}, open_debate + floor,
                                       outcome="You yielded the floor.", private=True)
        actions[f"{name}_recognize"] = act(chair or members, "Give the floor to a member whose hand is raised.",
                                           {"action": "recognize", "who": "$params.who"},
                                           open_debate + [{"expr": f"$len($world.{name}.hands) > 0", "why": "No hands are raised."}],
                                           {"who": {"type": "entity", "of": members, "where": f"$it.id in $world.{name}.hands"}},
                                           private=True, outcome="You recognized {$params.who.name}.")
    if config.motions:
        actions[f"{name}_propose"] = act(members, "Move a motion for the body to decide.", {"action": "propose", "text": "$params.text"},
                                         open_debate + floor + [{"expr": f"$len($world.{name}.stack) == 0",
                                                                 "why": "A motion is already before the body."}],
                                         {"text": {**text, "description": "The motion, worded as the decision."}},
                                         outcome="You moved a motion.")
        actions[f"{name}_withdraw"] = act(members, "Withdraw your pending motion or amendment.", {"action": "withdraw"},
                                          open_debate + [{"expr": f"{top} != null and {top}.mover == $actor.id",
                                                          "why": "You have nothing pending to withdraw."}], outcome="Withdrawn.")
        if config.second:
            actions[f"{name}_second"] = act(members, "Second the proposal waiting for a second.", {"action": "second"},
                                            open_debate + [{"expr": f"{top} != null and {top}.status == 'proposed' and {top}.mover != $actor.id",
                                                            "why": "Nothing you can second is waiting."}], outcome="Seconded.")
        if config.amendments:
            actions[f"{name}_amend"] = act(members, "Propose new wording for the open motion (voted on before the motion).",
                                           {"action": "amend", "text": "$params.text"},
                                           open_debate + floor + [{"expr": f"{top} != null and {top}.kind == 'motion' and {top}.status == 'open'",
                                                                   "why": "There is no open motion to amend."}],
                                           {"text": {**text, "description": "The full motion as you would amend it."}},
                                           outcome="You moved an amendment.")
        call_when = open_debate + [{"expr": f"{top} != null and {top}.status == 'open'"
                                            + ("" if chair else f" and {top}.speeches >= {config.min_debate}"),
                                    "why": "There is no open question ready to be called."}]
        actions[f"{name}_call_question"] = act(chair or members, "End debate and put the open question to a vote.",
                                               {"action": "call_question"}, call_when, outcome="You called the question.", terminal=True)
    vote_open = [{"expr": f"$world.{name}.phase == 'voting' and not ($actor.id in $world.{name}.ballots)",
                  "why": "There is no vote for you to cast."}]
    sealed = config.private
    actions[f"{name}_vote"] = act(members, "Vote on the question before the body.", {"action": "vote", "choice": "$params.choice"},
                                  vote_open, {"choice": {"type": "enum", "values": ["yes", "no", "abstain"]}},
                                  outcome="You voted {$params.choice}.", terminal=True, private=True,
                                  **({} if sealed else {"announce": "{$actor.name} votes {$params.choice}."}))
    if not sealed:
        actions[f"{name}_vote"]["private"] = False
    vote_names = [f"{name}_vote"]
    talk_names = [a for a in actions if a not in vote_names]
    discussion: Dict[str, Any] = {
        "name": name, "turns": "sequential", "actions": talk_names, "quiet": "skip", "passes": config.passes,
        "until": "$discussion_over()", "max_actions": 2,
        "brief": "Discuss. Anything said clears everyone's readiness; end your turn (or say you are ready) when you have "
                 "nothing to add.",
        "on_enter": [{"decision": name, "action": "open"}], "on_exit": [{"decision": name, "action": "close"}]}
    if chair:
        discussion["order"] = f"0 if $is($it, {chair}) else 1"
    if config.ready_when_silent:
        discussion["on_idle"] = [{"decision": name, "action": "idle"}]
    if config.when:
        discussion["when"] = config.when
    viewers = [members] + ([chair] if chair else [])
    return {
        "types": {members: {"props": {f"{name}_ready": {"type": "bool", "default": False,
                                                        "description": "Nothing more to add this round."}}}},
        "world": {name: {"type": "map", "default": _fresh(), "description": "Deliberation state."}},
        "records": {name: {"description": "Speeches, motions and seconds before the body.",
                           "fields": {"says": "text", "text": "text", "motion": "int"},
                           "show": "{author} {says}{$': ' if $it.text != '' else ''}{text}"}},
        "actions": actions,
        "stages": [discussion,
                   {"name": f"{name}_vote", "turns": "simultaneous", "actions": vote_names,
                    "when": f"$world.{name}.phase == 'voting'", "brief": "Vote yes, no or abstain on the question before the body.",
                    "on_exit": [{"decision": name, "action": "tally"}]}],
        "views": {f"{name}_house": {"for": viewers, "title": "The floor", "show": "{$house($actor)}"}},
    }
