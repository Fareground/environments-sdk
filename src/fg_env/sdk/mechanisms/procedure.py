"""Procedures: a state machine of named phases that compiles to ordinary stages.

.. code-block:: json

    "trial": {"kind": "procedure", "phases": {
        "opening": {"stages": [{"who": "$it.type == attorney", "actions": ["opening_statement"]}],
                    "next": [{"to": "evidence", "all_did": "opening_statement"}]},
        "evidence": {"stages": [...], "next": [{"to": "closing", "after": "$inputs.days"}]},
        "closing": {"stages": [...], "next": "deliberation"},
        "verdict": {"stages": [...], "terminal": true}}}

Each phase's stages run only while ``$world.<name>_phase`` is that phase (the stage's own ``when``
still applies), so tools, previews and checks follow the procedure. Transitions are checked at the
end of every round, in order; the first whose conditions all hold fires: its ``do``, the phase's
``on_exit``, the new phase's ``on_enter`` and ``say``. A phase therefore lasts at least one round.
``$world.<name>_round`` counts rounds in the current phase (1 in its first round),
``$world.<name>_since`` is the round it began and ``$world.<name>_history`` lists the phases entered.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from pydantic import Field, ValidationError, model_validator

from ..contract import StageSpec
from ..errors import RunError
from ..expr import truthy
from ..registry import MechanismError, effect_op, mechanism
from . import _common as common
from ._common import Config, Effects

__all__ = ["Transition", "PhaseDef", "ProcedureConfig"]

KIND = "procedure"


class Transition(Config):
    """A way out of a phase. Every condition given must hold; none given = after one round."""

    to: str = Field(..., description="The next phase.")
    when: Optional[str] = Field(None, description="An expression that must hold.")
    after: Union[int, str, None] = Field(None, description="At least this many rounds in the phase.")
    event: Optional[str] = Field(None, description="An event of this kind happened during the phase (an emit, a record, a vote).")
    all_did: Optional[str] = Field(None, description="Every agent who may take this action took it during the phase.")
    say: str = Field("", description="News when it fires (template).")
    do: Effects = Field(default_factory=list, description="Effects when it fires.")


class PhaseDef(Config):
    """One phase."""

    title: str = Field("", description="Name shown to agents.")
    brief: str = Field("", description="Stage brief for this phase's stages that give none (template).")
    stages: List[Dict[str, Any]] = Field(default_factory=list, description="Stages (ordinary stage fields) that run during the phase.")
    on_enter: Effects = Field(default_factory=list)
    on_exit: Effects = Field(default_factory=list)
    say: str = Field("", description="News when the phase begins (template).")
    next: Union[str, List[Transition]] = Field(default_factory=list, description="A phase name, or transitions tried in order.")
    terminal: bool = Field(False, description="The run ends at the end of the phase's first round (at once if it has no stages).")
    winner: Optional[str] = Field(None, description="Terminal phases: expression naming the winner(s).")

    @model_validator(mode="after")
    def _shape(self) -> "PhaseDef":
        if self.terminal and self.next:
            raise ValueError("a terminal phase has no `next`")
        if self.winner is not None and not self.terminal:
            raise ValueError("`winner` belongs to a terminal phase")
        return self

    def transitions(self) -> List[Transition]:
        return [Transition(to=self.next)] if isinstance(self.next, str) else list(self.next)


class ProcedureConfig(Config):
    """A state machine of phases."""

    phases: Dict[str, PhaseDef] = Field(
        ..., description="{phase: {title, brief, stages, on_enter, on_exit, say, next, terminal, winner}} in order. "
                         "`next` is a phase name or [{to, when, after, event, all_did, say, do}] tried in order at the end "
                         "of each round.")
    start: Optional[str] = Field(None, description="The first phase (default: the first listed).")
    view: bool = Field(True, description="Show every agent the current phase.")


@mechanism(KIND, ProcedureConfig,
           "A procedure (state machine): named phases, each with its own stages, entry/exit effects, news and "
           "transitions by condition, rounds in phase, an event, or everyone having acted; terminal phases end the run. "
           "Compiles to stages gated on $world.<name>_phase, so tools and previews follow the phase.",
           example={"kind": KIND, "phases": {
               "debate": {"stages": [{"actions": ["speak"]}], "next": [{"to": "vote", "after": 2}]},
               "vote": {"stages": [{"actions": ["vote"], "turns": "simultaneous"}], "terminal": True}}})
def _expand(name: str, cfg: ProcedureConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    if not cfg.phases:
        raise MechanismError("a procedure needs at least one phase", None, "phases")
    start = cfg.start or next(iter(cfg.phases))
    if start not in cfg.phases:
        raise MechanismError(f"'{start}' is not a phase", common.suggest(start, cfg.phases), "start")
    actions = contract.get("actions") or {}
    taken = {s.get("name") for s in contract.get("stages") or [] if isinstance(s, Mapping)}
    stages: List[Dict[str, Any]] = []
    for phase, spec in cfg.phases.items():
        field = f"phases.{phase}"
        if not common.NAME.match(phase):
            raise MechanismError(f"phase name '{phase}' must start with a letter and use letters, digits and _", None, field)
        for index, transition in enumerate(spec.transitions()):
            if transition.to not in cfg.phases:
                raise MechanismError(f"'{transition.to}' is not a phase", common.suggest(transition.to, cfg.phases),
                                     f"{field}.next[{index}].to")
            if transition.all_did is not None and transition.all_did not in actions:
                raise MechanismError(f"there is no action '{transition.all_did}'", common.suggest(transition.all_did, actions),
                                     f"{field}.next[{index}].all_did")
        stages += _stages(name, phase, spec, taken)
    phases = list(cfg.phases)
    fragment: Dict[str, Any] = {
        "world": {
            f"{name}_phase": {"type": "enum", "values": phases, "default": start, "description": "The current phase."},
            f"{name}_round": {"type": "int", "default": 0, "description": "Rounds in the current phase (1 in its first round)."},
            f"{name}_since": {"type": "int", "default": 0, "description": "Round the current phase began."},
            f"{name}_history": {"type": "list", "default": [], "description": "Phases entered, in order."},
        },
        "stages": stages,
        "events": [{"name": f"{name}_start", "phase": "start", "do": [{"procedure": name, "step": "start"}]},
                   {"name": f"{name}_advance", "phase": "end", "do": [{"procedure": name, "step": "end"}]}],
    }
    if cfg.view:
        titles = ", ".join(f"{_quote(p)}: {_quote(s.title or p.replace('_', ' '))}" for p, s in cfg.phases.items())
        fragment["defs"] = {f"{name}_title": {"expr": f"$get({{{titles}}}, $world.{name}_phase)",
                                              "description": "The current phase's title."}}
        fragment["views"] = {name: {"title": "Procedure",
                                    "show": f"Phase: {{${name}_title}} (round {{$world.{name}_round}} of this phase)."}}
    return fragment


def _quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _stages(name: str, phase: str, spec: PhaseDef, taken: set) -> List[Dict[str, Any]]:
    out = []
    gate = f"$world.{name}_phase == {_quote(phase)}"
    unnamed = 0
    for index, raw in enumerate(spec.stages):
        field = f"phases.{phase}.stages[{index}]"
        if not isinstance(raw, Mapping):
            raise MechanismError("a stage is an object of stage fields", None, field)
        stage = copy.deepcopy(dict(raw))
        if "name" not in stage:
            stage["name"] = phase if unnamed == 0 else f"{phase}_{index + 1}"
            unnamed += 1
        if stage["name"] in taken:
            raise MechanismError(f"a stage named '{stage['name']}' already exists", "give this stage its own `name`", f"{field}.name")
        taken.add(stage["name"])
        stage["when"] = f"{gate} and ({stage['when']})" if stage.get("when") else gate
        if spec.brief and not stage.get("brief"):
            stage["brief"] = spec.brief
        try:
            StageSpec.model_validate(stage)
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(p) for p in error["loc"])
            message = "is not a stage field" if error["type"] == "extra_forbidden" else error["msg"]
            raise MechanismError(message, None, f"{field}.{where}" if where else field) from None
        out.append(stage)
    return out


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------


def _holds(runner: Any, mech: str, phase: str, index: int, transition: Transition) -> bool:
    world = runner.world
    at = f"mechanisms.{mech}.phases.{phase}.next[{index}]"
    props = world.props
    if transition.after is not None:
        needed = common.whole(common.evaluate(world, transition.after, f"{at}.after"), f"{at}.after", low=0)
        if props[f"{mech}_round"] < needed:
            return False
    since = props[f"{mech}_since"]
    if transition.event is not None and not any(e.kind == transition.event for e in _since(world, since)):
        return False
    if transition.all_did is not None:
        by = common.by_types(world.contract.actions[transition.all_did])
        did = {e.actor for e in _since(world, since)
               if e.kind == "action" and e.data.get("action") == transition.all_did and e.data.get("success", True)}
        if not all(agent.id in did for agent in common.carriers(world, by)):
            return False
    if transition.when is not None and not truthy(common.evaluate(world, transition.when, f"{at}.when")):
        return False
    return True


def _since(world: Any, since: int) -> List[Any]:
    out = []
    for event in reversed(world.log):
        if event.round < since:
            break
        out.append(event)
    return out


def _news(runner: Any, mech: str, template: str, where: str) -> None:
    if template:
        text = runner.text(template, {})
        if text.strip():
            runner.world.emit(mech, text, data={"mechanism": KIND})


def _enter(runner: Any, mech: str, cfg: ProcedureConfig, phase: str, begins: int) -> None:
    world = runner.world
    world.set_world(f"{mech}_phase", phase)
    world.set_world(f"{mech}_since", begins)
    world.set_world(f"{mech}_round", 1 if begins == world.round else 0)
    world.set_world(f"{mech}_history", [*world.props[f"{mech}_history"], phase])
    spec = cfg.phases[phase]
    at = f"mechanisms.{mech}.phases.{phase}"
    runner.run(spec.on_enter, {}, f"{at}.on_enter")
    _news(runner, mech, spec.say, f"{at}.say")
    if spec.terminal and not spec.stages:
        _finish(runner, mech, phase, spec)


def _finish(runner: Any, mech: str, phase: str, spec: PhaseDef) -> None:
    winner = runner.eval(spec.winner, {}) if spec.winner is not None else None
    runner.world.request_end(phase, common.plain(winner), "")


def _check(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    name = effect.get("procedure")
    raw = checker.c.mechanisms.get(name)
    problems: List[Tuple[str, str, Optional[str]]] = []
    if effect.get("step") not in ("start", "end"):
        problems.append((f"{path}.step", "step is start or end", None))
    if not isinstance(raw, Mapping) or raw.get("kind") != KIND:
        return problems + [(f"{path}.procedure", f"'{name}' is not a declared {KIND} mechanism", None)]
    base = set(common.base_roots())
    for phase, spec in common.parsed(raw, ProcedureConfig).phases.items():
        at = f"mechanisms.{name}.phases.{phase}"
        for key in ("on_enter", "on_exit"):
            checker.effects(getattr(spec, key), f"{at}.{key}", base, {})
        checker.template(spec.say or None, f"{at}.say", None, base)
        checker.expr(spec.winner, f"{at}.winner", base)
        for index, transition in enumerate(spec.transitions()):
            where = f"{at}.next[{index}]"
            checker.expr(transition.when, f"{where}.when", base)
            checker.value(transition.after, f"{where}.after", base)
            checker.effects(transition.do, f"{where}.do", base, {})
            checker.template(transition.say or None, f"{where}.say", None, base)
    return problems


@effect_op("procedure", keys=("step",), required=("step",), literal=("procedure", "step"), check=_check,
           example='{"procedure": "trial", "step": "end"}  (start: enter or count the phase; end: try transitions; generated)')
def _procedure_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["procedure"]
    cfg = common.config(world, mech, KIND, ProcedureConfig, where)
    phase = world.props[f"{mech}_phase"]
    if effect["step"] == "start":
        if not world.props[f"{mech}_history"]:
            _enter(runner, mech, cfg, phase, world.round)
        else:
            world.set_world(f"{mech}_round", world.props[f"{mech}_round"] + 1)
        return
    if effect["step"] != "end":
        raise RunError("step is start or end", f"{where}.step")
    spec = cfg.phases[phase]
    if spec.terminal:
        _finish(runner, mech, phase, spec)
        return
    for index, transition in enumerate(spec.transitions()):
        if not _holds(runner, mech, phase, index, transition):
            continue
        at = f"mechanisms.{mech}.phases.{phase}.next[{index}]"
        runner.run(transition.do, {}, f"{at}.do")
        runner.run(spec.on_exit, {}, f"mechanisms.{mech}.phases.{phase}.on_exit")
        _news(runner, mech, transition.say, f"{at}.say")
        _enter(runner, mech, cfg, transition.to, world.round + 1)
        return
