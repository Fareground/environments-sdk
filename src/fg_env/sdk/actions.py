"""Actions as tools: which are legal, their JSON Schemas, argument validation, atomic apply.

Tool schemas live in :mod:`.action_schemas`, argument validation in :mod:`.action_validation`, and the
parameter limits both share in :mod:`.action_params`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..entity import Entity
from .action_faults import fault_reason
from .action_params import MAX_SAFE_INT, TEXT_MAX_LEN, _tidy
from .action_schemas import ActionSchemas, ToolSpec
from .action_validation import ActionValidation
from .assets.delivery import attached_ids
from .contract import ActionSpec, Contract, ParamSpec, RecordSpec, StageSpec
from .effects import EffectRunner
from .errors import RunError
from .probability import is_probability
from .expr import EVAL_BUDGET, ExprError, Scope, compile_expr, is_expr, shared_budget, truthy
from .template import compile_template, format_value
from .world import Abort, SdkWorld, _plain

__all__ = ["ACTION_BUDGET", "TEXT_MAX_LEN", "MAX_SAFE_INT", "ToolSpec", "Outcome", "ActionBook", "stage_actions"]

#: Work one action application may do in total (all its conditions, effects and templates).
ACTION_BUDGET = 5 * EVAL_BUDGET


@dataclass
class Outcome:
    ok: bool
    text: str
    success: bool = True
    params: Dict[str, Any] = field(default_factory=dict)
    #: The assets the action's `attach` delivers to its actor.
    assets: List[str] = field(default_factory=list)


def stage_actions(contract: Contract, stage: StageSpec, type_name: str) -> List[str]:
    """Action names available to ``type_name`` during ``stage`` (before per-turn legality)."""
    spec = stage.actions
    if spec == "all":
        names = list(contract.actions)
    elif isinstance(spec, list):
        names = list(spec)
    elif isinstance(spec, dict):
        names = []
        for kind in reversed(contract.lineage(type_name)):  # the most specific type's list wins
            if kind in spec:
                names = list(spec[kind])
                break
    else:
        names = []
    out = []
    for name in names:
        action = contract.actions.get(name)
        if action is None:
            continue
        by = [action.by] if isinstance(action.by, str) else action.by
        if any(contract.is_a(type_name, allowed) for allowed in by):
            out.append(name)
    return out


class ActionBook(ActionSchemas, ActionValidation):
    def __init__(self, contract: Contract, world: SdkWorld, effects: EffectRunner):
        self.contract = contract
        self.world = world
        self.effects = effects
        #: Shared tool name → the actions offered inside it, in declaration order.
        self.groups: Dict[str, List[str]] = {}
        for name, spec in contract.actions.items():
            if spec.tool is not None:
                self.groups.setdefault(spec.tool, []).append(name)

    # -- legality -------------------------------------------------------------

    def blocked(self, actor: Entity, name: str, used_turn: Dict[str, int], used_round: Dict[str, int]) -> Optional[str]:
        """Why ``name`` is not legal for ``actor`` right now, or None when it is."""
        spec = self.contract.actions[name]
        if not actor.alive:
            return "you are no longer active"
        if spec.per_turn is not None and used_turn.get(name, 0) >= spec.per_turn:
            return f"{name} can be used {spec.per_turn} time(s) per turn"
        if spec.per_round is not None and used_round.get(name, 0) >= spec.per_round:
            return f"{name} can be used {spec.per_round} time(s) per round"
        refused = self._unmet(actor, name, None)
        if refused is not None:
            return refused
        for pname, param in spec.params.items():
            if not self._required(param) or self._depends_on_params(param):
                continue
            if param.type == "entity" and not self._choices(actor, name, pname, param, first=True):
                return f"there is no {param.of or 'target'} you can choose for {pname} right now"
            if param.type in ("number", "int"):
                empty = self._empty_range(actor, param)
                if empty is not None:
                    return f"there is no valid {pname} right now ({empty})"
            if param.type == "enum" and isinstance(param.values, str) and self._static(actor, param.values) == []:
                return f"there is no value you can choose for {pname} right now"
        return None

    def _unmet(self, actor: Entity, name: str, params: Optional[Dict[str, Any]]) -> Optional[str]:
        """The `why` of the first requirement that does not hold: those over $actor alone (``params`` None), or
        those that read $params."""
        scope: Optional[Scope] = None
        for index, condition in enumerate(self.contract.actions[name].when):
            compiled = compile_expr(condition.expr)
            if ("params" in compiled.roots) is not (params is not None):
                continue
            if scope is None:  # built for the first requirement evaluated
                scope = self.world.scope(actor=actor) if params is None else self.world.scope(actor=actor, params=params)
            try:
                ok = truthy(compiled(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{name}.when[{index}]") from None
            if not ok:
                return (condition.why or "its requirements are not met").rstrip(". ")
        return None

    def _empty_range(self, actor: Entity, param: ParamSpec) -> Optional[str]:
        """The bounds, when no value lies between them right now (min above max)."""
        low, high = _tidy(self._static(actor, param.min)), _tidy(self._static(actor, param.max))
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (low, high)):
            return None
        least, most = (math.ceil(low), math.floor(high)) if param.type == "int" else (low, high)
        return f"at least {format_value(low)} and at most {format_value(high)}" if least > most else None

    @staticmethod
    def _required(param: ParamSpec) -> bool:
        return param.required if param.required is not None else param.default is None

    @staticmethod
    def _depends_on_params(param: ParamSpec) -> bool:
        return param.where is not None and "params" in compile_expr(param.where).roots

    def _choices(self, actor: Entity, action: str, pname: str, param: ParamSpec,
                 params: Optional[Dict[str, Any]] = None, first: bool = False) -> List[Entity]:
        """Entities that qualify. A `where` over earlier params is applied once they are known
        (at validation); before that (tool schemas) every entity of the type is listed. With
        ``first``, stop at the first one (enough to know whether any qualifies)."""
        if param.of is None:
            raise RunError("an entity parameter needs `of` (the entity type)", f"actions.{action}.params.{pname}")
        items = self.world.entities_of(param.of)
        if param.where is None:
            return items
        expr = compile_expr(param.where)
        if "params" in expr.roots and params is None:
            return items
        out = []
        base = self.world.scope(actor=actor, viewer=actor, params=params or {})
        ruled_out = expr.rules_out(base)
        for position, item in enumerate(items):
            if ruled_out is not None and ruled_out(item):
                continue
            try:
                if truthy(expr(base.child(it=item, i=position))):
                    out.append(item)
                    if first:
                        break
            except ExprError as exc:
                raise RunError(str(exc), f"actions.{action}.params.{pname}.where") from None
        return out

    def fill_dependent(self, actor: Entity, name: str, args: Dict[str, Any],
                       pick: Callable[[List[Entity]], Optional[Entity]]) -> Dict[str, Any]:
        """``args`` with each entity argument whose choices depend on earlier arguments set to ``pick`` of the
        entities that qualify given them — for participants that choose arguments without reading the rules. It
        stops at the first earlier argument that is missing or invalid: validation then says what to fix."""
        spec = self.contract.actions[name]
        if not any(p.type == "entity" and self._depends_on_params(p) for p in spec.params.values()):
            return args
        filled: Dict[str, Any] = dict(args)
        params: Dict[str, Any] = {}
        for pname, param in spec.params.items():
            if param.type == "entity" and self._depends_on_params(param):
                chosen = pick(self._choices(actor, name, pname, param, params))
                if chosen is None:
                    filled.pop(pname, None)
                    break
                params[pname], filled[pname] = chosen, chosen.id
                continue
            raw = filled.get(pname)
            if raw is None:
                break
            value, problem = self._value(actor, name, pname, param, raw, params)
            if problem:
                break
            params[pname] = value
        return filled

    # -- apply ---------------------------------------------------------------------

    def apply(self, actor: Entity, name: str, params: Dict[str, Any]) -> Outcome:
        """Apply atomically. A `fail` effect or failed transfer rolls back and returns ok=False.
        Everything the action evaluates shares one work budget, so a loop of effects is bounded
        as a whole, not only each expression in it."""
        with shared_budget(ACTION_BUDGET, f"actions.{name}"):
            return self._apply(actor, name, params)

    def _apply(self, actor: Entity, name: str, params: Dict[str, Any], trial: bool = False) -> Outcome:
        """Apply atomically. A ``trial`` (a dry run, rolled back by the caller) leaves out the default outcome text,
        the announcement and its event: they cannot fail or draw, and a rollback would undo them unseen. The action
        draws from its actor's own stream, so it never shifts another agent's luck or the world's; a refusal gives
        its draws back, so retrying rolls the same luck (see :class:`~fg_env.sdk.seeds.DrawSite`)."""
        with self.world.drawing_at(f"actions.{name}@{actor.id}"):
            return self._apply_drawn(actor, name, params, trial)

    def _apply_drawn(self, actor: Entity, name: str, params: Dict[str, Any], trial: bool) -> Outcome:
        """:meth:`_apply` inside the action's draw site."""
        spec: ActionSpec = self.contract.actions[name]
        world = self.world
        mark = world.journal.mark()
        vars: Dict[str, Any] = {"actor": actor, "params": params}
        path = f"actions.{name}"
        success = True
        log_mark = world.log[-1].seq if world.log else 0
        record_mark = world._record_seq
        try:
            if spec.chance is not None:
                probability = compile_expr(spec.chance)(world.scope(**vars)) if is_expr(spec.chance) else spec.chance
                if not is_probability(probability):
                    raise RunError(f"chance must be a number from 0 to 1, got {probability!r}", f"{path}.chance")
                success = world.rng.random() < probability
            self.effects.run(spec.do if success else spec.otherwise, vars, f"{path}.{'do' if success else 'otherwise'}")
            text = self._render(spec.outcome, {**vars, "viewer": actor}, f"{path}.outcome") if spec.outcome else \
                "" if trial else self._default_outcome(name, params, success)
            assets = attached_ids(world, spec.attach, world.scope(**vars), f"{path}.attach") if spec.attach else []
            announce = spec.announce
            if trial:
                if announce is not None and not spec.private:
                    self._render(announce, vars, f"{path}.announce")
                world.touch()  # the announcement would have changed the state version
            elif not spec.private:
                public = {} if self._sealed() else self._public_params(params, self._posted_since(record_mark))
                if announce is not None:
                    line = self._render(announce, vars, f"{path}.announce")
                elif _notified_since(world, log_mark):
                    line = ""  # the posted entry itself is the news
                else:
                    line = self._default_announce(actor, name, public, success)
                # Public: every agent may learn of it; the actor's own announcement is
                # filtered out of its news by perception.
                announcement = world.emit("action", line, actor=actor.id, to=None,
                                          data={"action": name, "params": _plain(public), "success": success})
                _first_in_order(world, log_mark, announcement)
            else:
                world.emit("action", "", actor=actor.id, to=(actor.id,),
                           data={"action": name, "params": _plain(params), "success": success, "private": True})
        except Abort as abort:
            world.journal.rollback(mark)
            return Outcome(False, abort.reason, False, params)
        except ExprError as exc:
            world.journal.rollback(mark)
            raise RunError(str(exc), path) from None
        except RunError:
            world.journal.rollback(mark)
            raise
        return Outcome(True, text, success, params, assets)

    def duration(self, actor: Entity, name: str, params: Dict[str, Any]) -> float:
        """How long the action takes on a continuous clock (0 when it declares no duration)."""
        raw = self.contract.actions[name].duration
        if raw is None:
            return 0.0
        try:
            value = compile_expr(raw)(self.world.scope(actor=actor, params=params)) if is_expr(raw) else raw
        except ExprError as exc:
            raise RunError(str(exc), f"actions.{name}.duration") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise RunError(f"duration must be a number ≥ 0, got {format_value(value)}", f"actions.{name}.duration")
        return float(value)

    def ends_turn(self, actor: Entity, name: str, params: Dict[str, Any]) -> bool:
        terminal = self.contract.actions[name].terminal
        if isinstance(terminal, bool):
            return terminal
        try:
            return truthy(compile_expr(terminal)(self.world.scope(actor=actor, params=params)))
        except ExprError as exc:
            raise RunError(str(exc), f"actions.{name}.terminal") from None

    def dry_run(self, actor: Entity, name: str, params: Dict[str, Any]) -> Optional[str]:
        """Apply and roll back, to catch a doomed sealed choice at submit. Returns the refusal, or None. The rollback
        gives back the action's draws too, so the real call rolls what the dry run rolled."""
        world = self.world
        mark = world.journal.mark()
        picker, world.chance_picker = world.chance_picker, None  # a trial roll is sampled, never asked for
        try:
            with shared_budget(ACTION_BUDGET, f"actions.{name}"):
                outcome = self._apply(actor, name, params, trial=True)
        finally:
            world.journal.rollback(mark)
            world.chance_picker = picker
        return None if outcome.ok else outcome.text

    def refusal(self, actor: Entity, name: str, params: Dict[str, Any]) -> Optional[str]:
        """:meth:`dry_run` for code that only asks whether a call would work (tool probes, legal-call listings): a rule
        that fails for the call refuses it, as it would if an agent made it."""
        try:
            return self.dry_run(actor, name, params)
        except RunError as exc:
            return fault_reason(exc)

    def _posted_since(self, record_mark: int) -> List[Tuple[RecordSpec, Dict[str, Any]]]:
        """Entries posted after ``record_mark``, with their record's spec."""
        if self.world._record_seq == record_mark:
            return []
        posted: List[Tuple[RecordSpec, Dict[str, Any]]] = []
        for name, spec in self.contract.records.items():
            for entry in reversed(self.world.records_store.get(name, [])):
                if entry["seq"] <= record_mark:
                    break
                posted.append((spec, entry))
        return posted

    def _sealed(self) -> bool:
        """Whether actions now commit as a simultaneous stage's sealed choices: announced without their arguments,
        so a losing sealed bid stays sealed unless the action's `announce` says otherwise."""
        stage = self.world.stage
        return any(spec.name == stage and spec.turns == "simultaneous" for spec in self.contract.stage_list())

    @staticmethod
    def _public_params(params: Dict[str, Any], posted: Sequence[Tuple[RecordSpec, Dict[str, Any]]]) -> Dict[str, Any]:
        """The arguments an announcement may repeat. An entry that is not broadcast to everyone
        (a record that does not notify, a directed or restricted entry) keeps its content to
        its own audience, so arguments carried into it are left out."""
        kept = [entry.get(field) for spec, entry in posted
                if not spec.notify or entry.get("to") is not None or spec.visible != "all"
                for field in spec.fields]
        if not kept:
            return params
        return {k: v for k, v in params.items() if not _carried(_plain(v), kept)}

    def _render(self, template: str, vars: Dict[str, Any], path: str) -> str:
        try:
            return compile_template(template, None).render(self.world.scope(**vars))
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    @staticmethod
    def _args_text(params: Dict[str, Any]) -> str:
        parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
        return f" ({', '.join(parts)})" if parts else ""

    def _default_outcome(self, name: str, params: Dict[str, Any], success: bool) -> str:
        verb = name.replace("_", " ")
        return f"Done: {verb}{self._args_text(params)}." if success else f"{verb.capitalize()} did not succeed."

    def _default_announce(self, actor: Entity, name: str, params: Dict[str, Any], success: bool) -> str:
        verb = name.replace("_", " ")
        suffix = "" if success else " — it did not succeed"
        return f"{actor.name}: {verb}{self._args_text(params)}{suffix}."


def _carried(value: Any, fields: Sequence[Any]) -> bool:
    """True when an argument value (or text containing it) is stored in one of ``fields``."""
    if value is None or isinstance(value, bool) or value == "":
        return False
    for stored in fields:
        if stored == value:
            return True
        if isinstance(value, str) and isinstance(stored, str) and value in stored:
            return True
        if isinstance(stored, (list, tuple)) and any(_carried(value, [item]) for item in stored):
            return True
    return False


def _notified_since(world: SdkWorld, log_mark: int) -> bool:
    """True when a record entry was delivered as news after log position ``log_mark``."""
    for event in reversed(world.log):
        if event.seq <= log_mark:
            return False
        if event.kind == "record":
            return True
    return False


def _first_in_order(world: SdkWorld, log_mark: int, event: Any) -> None:
    """Move an action's announcement ahead of the news its own effects produced."""
    log = world.log
    index = len(log) - 1
    while index > 0 and log[index - 1].seq > log_mark:
        index -= 1
    if log[index] is event:
        return
    log.remove(event)
    log.insert(index, event)
    for offset, item in enumerate(log[index:]):
        item.seq = log_mark + 1 + offset
