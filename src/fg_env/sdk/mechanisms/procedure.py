"""Procedures: rules of order — a state machine of named phases that compiles to ordinary stages, and
an optional response stack (the ``flow`` family's ``procedure`` mode).

.. code-block:: json

    "trial": {"kind": "flow", "mode": "procedure", "phases": {
        "opening": {"stages": [{"who": "$it.type == attorney", "actions": ["opening_statement"]}],
                    "next": [{"to": "evidence", "all_did": "opening_statement"}]},
        "evidence": {"stages": [...], "next": [{"to": "closing", "after": "$inputs.days"}]},
        "closing": {"stages": [...], "next": "deliberation"},
        "verdict": {"stages": [...], "terminal": true}}}

Unnamed stages are named ``<procedure>_<phase>`` (subsequent stages append their position), so
independent procedures can reuse phase names. Explicit stage names are preserved.

Each phase's stages run only while ``$world.<name>_phase`` is that phase (the stage's own ``when``
still applies), so tools, previews and checks follow the procedure. Transitions are checked at the
end of every round, in order; the first whose conditions all hold fires: its ``do``, the phase's
``on_exit``, the new phase's ``on_enter`` and ``say``. A phase therefore lasts at least one round.
``$world.<name>_round`` counts rounds in the current phase (1 in its first round),
``$world.<name>_since`` is the round it began and ``$world.<name>_history`` lists the phases entered.

With ``"stack": {...}`` the procedure also keeps a response stack (motions, objections, spells) whose
items are answered in response windows and resolve last in, first out — see
:mod:`.procedure_stack`. A procedure has phases, a stack, or both; every step runs through an action
of the ``flow`` op (``{"flow": "trial", "action": "push", "item": "exhibit", ...}``), and
``$stack(procedure, read, ...)`` reads the stack.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from pydantic import Field, ValidationError, model_validator

from ..contract import StageSpec
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function
from ..registry import MechanismError, family_action, mode
from . import _common as common
from ._common import Config, Effects, ToolsSetting, tools_field
from .procedure_stack import StackConfig, check_push, check_stack_rules, expand_stack, read_stack, run_step

__all__ = ["Transition", "PhaseDef", "ProcedureConfig"]

KEY = "flow.procedure"


class Transition(Config):
    """A way out of a phase. Every condition given must hold; none given = after one round."""

    to: str = Field(..., description="The next phase.")
    when: Optional[str] = Field(None, description="An expression that must hold.")
    after: Union[int, str, None] = Field(None, description="At least this many rounds in the phase.")
    event: Optional[str] = Field(None, description="An event of this kind happened during the phase (an emit, a record, a vote).")
    all_did: Optional[str] = Field(None, description="Every living entity of the action's `by` types took it during "
                                 "the phase, in any stage. Action conditions and stage filters do not narrow this group; "
                                 "other procedures using the same action can share completion evidence.")
    say: str = Field("", description="News when it fires (template).")
    do: Effects = Field(default_factory=list, description="Effects when it fires.")


class PhaseDef(Config):
    """One phase."""

    title: str = Field("", description="Name shown to agents.")
    brief: str = Field("", description="Stage brief for this phase's stages that give none (template).")
    stages: List[Dict[str, Any]] = Field(default_factory=list, description="Stages (ordinary stage fields) that run during the phase.")
    on_enter: Effects = Field(default_factory=list, description="Effects when the phase begins.")
    on_exit: Effects = Field(default_factory=list, description="Effects when the phase ends.")
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
    """Rules of order: a state machine of phases, a response stack, or both."""

    phases: Dict[str, PhaseDef] = Field(
        default_factory=dict,
        description="{phase: {title, brief, stages, on_enter, on_exit, say, next, terminal, winner}} in order. "
                    "`next` is a phase name or [{to, when, after, event, all_did, say, do}] tried in order at the end "
                    "of each round.")
    start: Optional[str] = Field(None, description="The first phase (default: the first listed).")
    views: bool = Field(True, description="Show every agent the current phase.")
    stack: Optional[StackConfig] = Field(
        None, description="A response stack: items pushed by `<name>_<kind>` tools or the `push` action, answered in the "
                          "window stage `<name>_stack` (push an answer or `<name>_pass`) and resolved last in, first out.")
    tools: ToolsSetting = tools_field()


@mode("flow", "procedure", ProcedureConfig,
      "Rules of order. Phases: a state machine of named phases, each with its own stages, entry/exit effects, news "
      "and transitions by condition, rounds in phase, an event, or everyone having acted; terminal phases end the "
      "run; compiles to stages gated on $world.<name>_phase. Stack: items (motions, objections, spells) pushed by "
      "`<name>_<kind>` tools, answered in response windows by the agents each kind names, resolved last in, first "
      "out with each kind's effects (the `counter` action removes one unresolved); read it with $stack(name, read).",
      example={"phases": {
          "debate": {"stages": [{"actions": ["speak"]}], "next": [{"to": "vote", "after": 2}]},
          "vote": {"stages": [{"actions": ["vote"], "turns": "simultaneous"}], "terminal": True}}}, ends=lambda cfg: any(phase.terminal for phase in cfg.phases.values()))
def _expand(name: str, cfg: ProcedureConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    if not cfg.phases and cfg.stack is None:
        raise MechanismError("a procedure needs phases, a stack, or both", 'e.g. "phases": {"debate": {...}} or "stack": {...}',
                             "phases")
    fragment = _phases(name, cfg, contract) if cfg.phases else {}
    if cfg.stack is not None:
        for section, value in expand_stack(name, cfg.stack, contract).items():
            current = fragment.get(section)
            fragment[section] = current + value if isinstance(current, list) else {**current, **value} if current else value
    return fragment


def _phases(name: str, cfg: ProcedureConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
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
        "events": [{"name": f"{name}_start", "phase": "start", "do": [{"flow": name, "action": "start"}]},
                   {"name": f"{name}_advance", "phase": "end", "do": [{"flow": name, "action": "advance"}]}],
    }
    if cfg.views:
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
            stage["name"] = f"{name}_{phase}" if unnamed == 0 else f"{name}_{phase}_{index + 1}"
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
    if transition.when is not None and not common.condition(world, transition.when, f"{at}.when"):
        return False
    return True


def _since(world: Any, since: int) -> List[Any]:
    out = []
    for event in reversed(world.log):
        if event.round < since:
            break
        out.append(event)
    return out


def _news(runner: Any, mech: str, template: str) -> None:
    if template:
        text = runner.text(template, {})
        if text.strip():
            runner.world.emit(mech, text, data={"mechanism": KEY})


def _enter(runner: Any, mech: str, cfg: ProcedureConfig, phase: str, begins: int) -> None:
    world = runner.world
    world.set_world(f"{mech}_phase", phase)
    world.set_world(f"{mech}_since", begins)
    world.set_world(f"{mech}_round", 1 if begins == world.round else 0)
    world.set_world(f"{mech}_history", [*world.props[f"{mech}_history"], phase])
    spec = cfg.phases[phase]
    runner.run(spec.on_enter, {}, f"mechanisms.{mech}.phases.{phase}.on_enter")
    _news(runner, mech, spec.say)
    if spec.terminal and not spec.stages:
        _finish(runner, phase, spec)


def _finish(runner: Any, phase: str, spec: PhaseDef) -> None:
    winner = runner.eval(spec.winner, {}) if spec.winner is not None else None
    runner.world.request_end(phase, common.plain(winner), "")


def _start(runner: Any, mech: str, cfg: ProcedureConfig) -> None:
    world = runner.world
    if not world.props[f"{mech}_history"]:
        _enter(runner, mech, cfg, world.props[f"{mech}_phase"], world.round)
    else:
        world.set_world(f"{mech}_round", world.props[f"{mech}_round"] + 1)


def _advance(runner: Any, mech: str, cfg: ProcedureConfig) -> None:
    world = runner.world
    phase = world.props[f"{mech}_phase"]
    spec = cfg.phases[phase]
    if spec.terminal:
        _finish(runner, phase, spec)
        return
    for index, transition in enumerate(spec.transitions()):
        if not _holds(runner, mech, phase, index, transition):
            continue
        at = f"mechanisms.{mech}.phases.{phase}.next[{index}]"
        runner.run(transition.do, {}, f"{at}.do")
        runner.run(spec.on_exit, {}, f"mechanisms.{mech}.phases.{phase}.on_exit")
        _news(runner, mech, transition.say)
        _enter(runner, mech, cfg, transition.to, world.round + 1)
        return


# ---------------------------------------------------------------------------
# The flow op's procedure actions
# ---------------------------------------------------------------------------

#: action → (its keys, required keys, generated by the mechanism itself, what it needs: phases | stack, example keys, what it does).
_ACTIONS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], bool, str, str, str]] = {
    "start": ((), (), True, "phases", "", "enter or count the phase; generated at the start of each round"),
    "advance": ((), (), True, "phases", "", "try the phase's transitions; generated at the end of each round"),
    "push": (("item", "params", "who"), ("item",), False, "stack", '"item": "exhibit", "params": {"name": "$params.name"}',
             "push an item of a declared kind with its params; who pushes defaults to $actor"),
    "pass": (("who",), (), False, "stack", "", "let the item on top of the stack stand; who defaults to $actor"),
    "counter": (("target",), (), False, "stack", "",
                "remove an item without resolving it: target is an item id, default the item below the one resolving, "
                "else the top"),
    "idle": (("who",), (), True, "stack", "", "the actor ended a window turn without acting"),
    "close": ((), (), True, "stack", "", "the window stage ended: answers still owed pass"),
}


def _check(action: str, needs: str) -> Callable[[Any, Dict[str, Any], str], List[Tuple[str, str, Optional[str]]]]:
    def check(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
        name = effect["flow"]
        cfg = common.parsed(checker.c.mechanisms[name], ProcedureConfig)
        problems: List[Tuple[str, str, Optional[str]]] = []
        if needs == "phases" and not cfg.phases:
            problems.append((f"{path}.action", f"the {name} procedure has no phases", None))
        elif needs == "stack" and cfg.stack is None:
            problems.append((f"{path}.action", f"the {name} procedure has no stack", 'declare "stack": {"who": ..., "kinds": {...}}'))
        elif action == "push" and cfg.stack is not None:
            problems += check_push(name, cfg.stack, effect, path)
        checked = checker.__dict__.setdefault("_procedures_checked", set())
        if name not in checked:  # the rules hold procedure actions themselves: check them once per procedure
            checked.add(name)
            _check_rules(checker, name, cfg)
        return problems

    return check


def _check_rules(checker: Any, name: str, cfg: ProcedureConfig) -> None:
    if cfg.stack is not None:
        check_stack_rules(checker, name, cfg.stack)
    base = set(common.base_roots())
    for phase, spec in cfg.phases.items():
        at = f"mechanisms.{name}.phases.{phase}"
        for key in ("on_enter", "on_exit"):
            checker.effects(getattr(spec, key), f"{at}.{key}", base, {})
        checker.template(spec.say or None, f"{at}.say", None, base)
        checker.expr(spec.winner, f"{at}.winner", base)
        for index, transition in enumerate(spec.transitions()):
            where = f"{at}.next[{index}]"
            _check_shared_completion(checker, name, transition, where)
            _check_completion_scope(checker, transition, where)
            checker.expr(transition.when, f"{where}.when", base)
            checker.value(transition.after, f"{where}.after", base)
            checker.effects(transition.do, f"{where}.do", base, {})
            checker.template(transition.say or None, f"{where}.say", None, base)


def _check_shared_completion(checker: Any, name: str, transition: Transition, path: str) -> None:
    if transition.all_did is None:
        return
    users = checker.__dict__.get("_procedure_completion_users")
    if users is None:
        users = {}
        for owner, raw in checker.c.mechanisms.items():
            if raw.get("kind") != "flow" or raw.get("mode") != "procedure":
                continue
            cfg = common.parsed(raw, ProcedureConfig)
            for phase in cfg.phases.values():
                for step in phase.transitions():
                    if step.all_did is not None:
                        users.setdefault(step.all_did, set()).add(owner)
        checker.__dict__["_procedure_completion_users"] = users
    others = sorted(users.get(transition.all_did, set()) - {name})
    if others:
        checker.warn(
            f"{path}.all_did",
            f"all_did '{transition.all_did}' is also used by procedures {', '.join(others)}; "
            "it counts successful actions in any stage during this phase, so overlapping procedures "
            "can complete from the same action",
            "For independent approvals, use distinct action names per procedure or a transition `when` "
            "that checks procedure-specific state. Keep the shared action if shared completion is intended.",
        )


def _check_completion_scope(checker: Any, transition: Transition, path: str) -> None:
    if transition.all_did is None:
        return
    action = checker.c.actions.get(transition.all_did)
    if action is None:
        return
    for condition in action.when:
        try:
            roots = compile_expr(condition.expr).roots
        except ExprError:
            continue  # The action checker reports malformed conditions at their authored path.
        if "actor" in roots and "params" not in roots:
            checker.warn(
                f"{path}.all_did",
                f"all_did '{transition.all_did}' includes every living entity of its by types, "
                "including actors excluded by its actor conditions",
                "If only a cohort must act, use a transition `when` with a filtered completion condition; "
                "keep all_did when every actor must eventually act.",
            )
            return


def _runner(action: str, needs: str) -> Callable[[Any, Dict[str, Any], Dict[str, Any], str], None]:
    def run(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        mech = effect["flow"]
        cfg = common.config(runner.world, mech, KEY, ProcedureConfig, where)
        if needs == "stack":
            if cfg.stack is None:
                raise RunError(f"the {mech} procedure has no stack", f"{where}.action")
            run_step(runner, mech, cfg.stack, action, effect, vars, where)
        elif not cfg.phases:
            raise RunError(f"the {mech} procedure has no phases", f"{where}.action")
        elif action == "start":
            _start(runner, mech, cfg)
        else:
            _advance(runner, mech, cfg)

    return run


def _register_actions() -> None:
    for action, (keys, required, internal, needs, fields, doc) in _ACTIONS.items():
        example = '{"flow": "trial", "action": "' + action + '"' + (f", {fields}" if fields else "") + f"}}  ({doc})"
        family_action("flow", ("procedure",), action, keys=keys, required=required, literal=("item",) if "item" in keys else (),
                      check=_check(action, needs), internal=internal, example=example)(_runner(action, needs))


_register_actions()


@function("stack(procedure, read?, ...)",
          "A procedure's response stack. read: items (default; bottom first: [{id, kind, title, by, params, on, round, "
          "waiting}], `on` the id of the item it answers, `waiting` who still owes it an answer) | top (the top item or "
          "null) | waiting (ids who owe the top an answer; with an agent, whether it does) | can_push (kind, agent: whether "
          "it may push that kind now) | text (viewer?: the stack as lines, with what the viewer may answer).",
          min_args=1, max_args=4)
def _stack_function(call: Call) -> Any:
    world: Any = call.scope.world
    name = call.arg(0)
    if not isinstance(name, str):
        raise ExprError(f"$stack: the first argument is a procedure's name, got {name!r}", call.source)
    try:
        cfg = common.config(world, name, KEY, ProcedureConfig, "mechanisms")
    except RunError as exc:
        raise ExprError(f"$stack: {exc}", call.source) from None
    if cfg.stack is None:
        raise ExprError(f"$stack: the {name} procedure has no stack", call.source)
    return read_stack(call, name, cfg.stack)
