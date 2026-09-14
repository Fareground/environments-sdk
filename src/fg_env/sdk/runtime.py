"""The engine: rounds, stages, turns, events, ending, invariants, snapshots."""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..entity import Entity
from .actions import ActionBook, ToolSpec, stage_actions
from .build import build_world
from .contract import Contract, StageSpec
from .effects import EffectRunner
from .errors import InvariantViolation, RunError, SnapshotError
from .expr import ExprError, Untrusted, compile_expr, truthy
from .measure import RunResult, Stats, compute_outputs, sample_metrics
from .participants import Participant, resolve_participant
from .perception import Perception
from .seeds import SeedTree
from .session import END_TURN, ToolResult, Wake
from .template import compile_template, format_value
from .world import Abort, Entry, LogEvent, _plain

__all__ = ["Env", "SNAPSHOT_VERSION"]

SNAPSHOT_VERSION = 1


class _Memory:
    """What the engine remembers per agent between turns."""

    __slots__ = ("cursor", "views", "turns")

    def __init__(self) -> None:
        self.cursor = 0
        self.views: Dict[str, str] = {}
        self.turns = 0


class _Turn:
    def __init__(self, env: "Env", actor: Entity, stage: StageSpec, reason: str, staged: bool, peek: bool = False):
        self.env = env
        self.actor = actor
        self.stage = stage
        self.reason = reason
        self.staged = staged
        self.round = env.world.round
        memory = env._memory(actor.id)
        self._since = memory.cursor
        self._views = dict(memory.views) if peek else memory.views
        self._brief: Optional[str] = None
        self._update: Optional[str] = None
        self.calls_left = stage.max_calls
        self.actions_left = stage.max_actions
        self.done = False
        self.used: Dict[str, int] = {}
        self.intents: List[Tuple[str, Dict[str, Any]]] = []
        #: What this agent already did (sequential) or submitted (simultaneous) this turn, as $pending.
        self.pending: List[Dict[str, Any]] = []
        self.stats = Stats(wakes=1)
        self._offered = False
        self._tools: Optional[List[ToolSpec]] = None
        env._turn_count += 1
        self.number = env._turn_count  # assigned in deterministic order, before any concurrency

    # Brief and update render on first read, so coded participants that never read them cost nothing.

    @property
    def brief(self) -> str:
        if self._brief is None:
            with self.env._lock:
                self._brief = self.env._brief(self.actor)
            self.stats.brief_chars = len(self._brief)
            self.stats.brief_reads = 1
        return self._brief

    @property
    def update(self) -> str:
        if self._update is None:
            with self.env._lock:
                self._update = self.env.perception.update(self.actor, self.stage, self.reason, self._since, self._views)
            self.stats.update_chars = len(self._update)
            self.stats.update_reads = 1
        return self._update

    # -- tools ------------------------------------------------------------------

    def _legal(self) -> List[str]:
        if self.actions_left <= 0:
            return []
        env = self.env
        names = stage_actions(env.contract, self.stage, self.actor.entity_type)
        used_round = env._used_round.get(self.actor.id, {})
        return [n for n in names if env.actions.blocked(self.actor, n, self.used, used_round) is None]

    def tools(self) -> List[ToolSpec]:
        if self.done:
            return []
        if self._tools is not None:  # nothing changed since the last look (reset by every call)
            return self._tools
        env = self.env
        with env._lock:
            tools = [env.actions.tool(self.actor, name, self.staged) for name in self._legal()]
        looks = env.perception.look_views(self.actor, self.stage)
        if looks:
            tools.append(ToolSpec("look", "Show one of these views: " + ", ".join(looks) + ".", {
                "type": "object", "properties": {"view": {"type": "string", "enum": looks}},
                "required": ["view"], "additionalProperties": False}, "look"))
        if env._inspectable:
            tools.append(ToolSpec("inspect", "Details of one entity by id (uses one tool call).", {
                "type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"],
                "additionalProperties": False}, "look"))
        if not self._must_act_now(tools):
            end_text = "Finish your turn." if not self.staged else "Finish your turn (your choices are submitted)."
            tools.append(ToolSpec(END_TURN, end_text, {"type": "object", "properties": {}, "additionalProperties": False},
                                  "end", True))
        if not self._offered:
            self.stats.tools_offered += len(tools)
            self._offered = True
        self._tools = tools
        return tools

    def _must_act_now(self, tools: List[ToolSpec]) -> bool:
        acted = self.actions_left < self.stage.max_actions or bool(self.intents)
        return self.stage.must_act and not acted and any(t.kind == "act" for t in tools)

    # -- calls -------------------------------------------------------------------

    def call(self, name: str, args: Optional[Dict[str, Any]]) -> ToolResult:
        with self.env._lock:
            self._tools = None
            return self._call(name, args)

    def _call(self, name: str, args: Optional[Dict[str, Any]]) -> ToolResult:
        if self.done:
            return ToolResult(False, "Your turn is already over; nothing was done.", True, dict(_ENDED))
        if self.calls_left <= 0:
            self.done = True
            return ToolResult(False, "No tool calls left this turn; your turn is over.", True)
        self.calls_left -= 1
        self.stats.calls += 1
        env = self.env
        if name == END_TURN:
            if self.stage.must_act and self.actions_left == self.stage.max_actions and not self.intents and self._legal():
                self.stats.invalid_calls += 1
                return self._after(ToolResult(False, f"You must act during {self.stage.name}. Available actions: "
                                                     f"{', '.join(self._legal())}.", data=_INVALID))
            self.done = True
            return ToolResult(True, "Turn ended.", True)
        if name == "look":
            return self._look(args)
        if name == "inspect":
            return self._inspect(args)
        spec = env.contract.actions.get(name)
        available = stage_actions(env.contract, self.stage, self.actor.entity_type)
        if spec is None or name not in available:
            self.stats.invalid_calls += 1
            legal = ", ".join(self._legal()) or "none"
            why = "is not a tool" if spec is None else f"is not available during {self.stage.name}"
            return self._after(ToolResult(False, f"'{name}' {why}. Available actions: {legal}.", data=_INVALID))
        if self.actions_left <= 0:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, "You have no actions left this turn; call end_turn.", data=_INVALID))
        blocked = env.actions.blocked(self.actor, name, self.used, env._used_round.get(self.actor.id, {}))
        if blocked:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"You cannot {name.replace('_', ' ')} now: {blocked}.", data=_INVALID))
        params, problem = env.actions.validate(self.actor, name, args)
        if problem:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"{name} was not done: {problem}. Correct the arguments and call again.",
                                          data=_INVALID))
        if self.staged:
            refusal = env.actions.dry_run(self.actor, name, params)
            if refusal is not None:
                self.stats.rejected_actions += 1
                return self._after(ToolResult(False, refusal, data=_REJECTED))
            self.intents.append((name, dict(args or {})))
            self.pending.append({"action": name, **_plain(params)})
            self._count(name)
            ended = env.actions.ends_turn(self.actor, name, params) or self.actions_left <= 0
            text = f"Submitted {name.replace('_', ' ')}{_args_text(params)}; it resolves when everyone has chosen."
            return self._after(ToolResult(True, text, ended))
        outcome = env.actions.apply(self.actor, name, params)
        if outcome.ok:
            self._count(name)
            self.pending.append({"action": name, **_plain(params)})
            env._after_commit(f"actions.{name}")
        if not outcome.ok:
            self.stats.rejected_actions += 1
            return self._after(ToolResult(False, outcome.text, data=_REJECTED))
        self.stats.actions += 1
        ended = env.actions.ends_turn(self.actor, name, params) or self.actions_left <= 0 or env.world.end_request is not None
        return self._after(ToolResult(True, outcome.text, ended, {"success": outcome.success}))

    def _count(self, name: str) -> None:
        self.used[name] = self.used.get(name, 0) + 1
        per_round = self.env._used_round.setdefault(self.actor.id, {})
        per_round[name] = per_round.get(name, 0) + 1
        self.actions_left -= 1

    def _after(self, result: ToolResult) -> ToolResult:
        if result.ended:
            self.done = True
        elif self.calls_left <= 0:
            self.done = True
            result.ended = True
            result.text += " (No tool calls left; your turn is over.)"
        return result

    def _may_inspect(self, target: Entity) -> bool:
        rule = self.env._inspect_rule(target.entity_type)
        if isinstance(rule, bool):
            return rule or target.id == self.actor.id
        try:
            return target.id == self.actor.id or truthy(
                compile_expr(rule)(self.env.world.scope(viewer=self.actor, it=target)))
        except ExprError as exc:
            raise RunError(str(exc), f"types.{target.entity_type}.inspect") from None

    def _look(self, args: Optional[Dict[str, Any]]) -> ToolResult:
        env = self.env
        name = (args or {}).get("view")
        looks = env.perception.look_views(self.actor, self.stage)
        if name not in looks:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"view must be one of: {', '.join(looks) or 'none'}."))
        text = env.perception.render_view(name, env.contract.views[name], self.actor)
        return self._after(ToolResult(True, text or "Nothing to show."))

    def _inspect(self, args: Optional[Dict[str, Any]]) -> ToolResult:
        env = self.env
        target = env.world.entity((args or {}).get("id"))
        if target is None or not target.alive or not self._may_inspect(target):
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, "No entity with that id is available to inspect.", data=_INVALID))
        specs = env.contract.props_of(target.entity_type)
        own = target.id == self.actor.id
        shown = [f"{k}: {format_value(v)}" for k, v in target.properties.items()
                 if own or not specs.get(k) or not specs[k].private]
        where = f" at {format_value(target.location_id)}" if target.location_id is not None else ""
        text = f"{target.name} [{target.id}] ({target.entity_type}){where}" + ("\n" + "\n".join(shown) if shown else "")
        return self._after(ToolResult(True, text))


def _entity_dict(entity: Entity) -> Dict[str, Any]:
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type, "alive": entity.alive,
            "at": entity.location_id, "props": dict(entity.properties)}


_INVALID = {"error": "invalid"}
_REJECTED = {"error": "rejected"}
_ENDED = {"error": "ended"}


def _args_text(params: Mapping[str, Any]) -> str:
    parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
    return f" ({', '.join(parts)})" if parts else ""


def _encode(value: Any) -> Any:
    """JSON-safe copy that keeps participant-text provenance."""
    if isinstance(value, Untrusted):
        return {"$untrusted": str.__str__(value)}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$untrusted"}:
            return Untrusted(value["$untrusted"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def contract_hash(contract: Contract) -> str:
    text = json.dumps(contract.model_dump(by_alias=True, exclude_defaults=True), sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class Env:
    """A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`."""

    def __init__(self, contract: Contract, inputs: Dict[str, Any], seed: int, arm: Optional[str] = None,
                 parallel: int = 8):
        self.contract = contract
        self.inputs = inputs
        self.seed = seed
        self.arm = arm
        self.parallel = max(1, parallel)
        self.seeds = SeedTree(seed)
        self.world = build_world(contract, inputs, self.seeds, arm)
        self.effects = EffectRunner(self.world)
        self.actions = ActionBook(contract, self.world, self.effects)
        self.perception = Perception(contract, self.world)
        self.stats = Stats()
        self.status = "ready"
        self.ended_by: Optional[str] = None
        self.error: Optional[str] = None
        self._memories: Dict[str, _Memory] = {}
        self._briefs: Dict[str, str] = {}
        self._used_round: Dict[str, Dict[str, int]] = {}
        self._fired_once: set = set()
        self._lock = threading.RLock()
        self._participants: Dict[str, Participant] = {}
        self._stop: Optional[Callable[["Env"], bool]] = None
        self._on_event: Optional[Callable[[Dict[str, Any]], None]] = None
        self._emitted = 0
        self._turn_count = 0
        self._inspectable = any(self._inspect_rule(kind) is not False for kind in contract.types)
        self._check_invariants("build")

    # -- public API ----------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.status in ("completed", "ended", "failed")

    @property
    def round(self) -> int:
        return self.world.round

    def run(self, participants: Any = None, *, rounds: Optional[int] = None,
            stop: Optional[Callable[["Env"], bool]] = None,
            on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
            raise_errors: bool = False) -> RunResult:
        """Run to the end (or ``rounds`` more rounds, or until ``stop(env)`` is true).

        ``participants`` is a callable for every agent, or a mapping from entity id, type or
        ``"*"`` to a participant (a callable, ``"random"``, ``"idle"``, ``"policy:<name>"``).
        Agents without one use their type's ``policy`` or ``"random"``.
        """
        self._bind(participants)
        self._stop, self._on_event = stop, on_event
        budget = rounds
        try:
            while not self.finished:
                if budget is not None:
                    if budget <= 0:
                        break
                    budget -= 1
                if self._stop is not None and self._stop(self):
                    self.status = "stopped"
                    break
                self._round()
        except (RunError, ExprError) as exc:
            self.status, self.error = "failed", str(exc)
            if raise_errors:
                raise
        self._flush_events()
        return self.result()

    def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """A copy of one entity: ``{id, name, type, alive, at, props}``, or None."""
        found = self.world.entities.get(entity_id)
        return _entity_dict(found) if found is not None else None

    def entities(self, type_name: Optional[str] = None, alive: bool = True) -> List[Dict[str, Any]]:
        """Copies of entities, optionally of one type (subtypes included) and only alive ones."""
        kinds = set(self.contract.subtypes(type_name)) if type_name else None
        return [_entity_dict(e) for e in self.world.entities.values()
                if (kinds is None or e.entity_type in kinds) and (e.alive or not alive)]

    @property
    def props(self) -> Dict[str, Any]:
        """A copy of the world's global properties."""
        return dict(self.world.props)

    def step(self, participants: Any = None) -> RunResult:
        """Run exactly one round."""
        return self.run(participants, rounds=1)

    def result(self) -> RunResult:
        outputs: Dict[str, Any] = {}
        issues: List[Dict[str, Any]] = []
        if self.status != "failed":  # unfinished runs get provisional outputs
            computed, problems = compute_outputs(self.contract, self.world)
            outputs, issues = computed, [p.to_dict() for p in problems]
        end = self.world.end_request or {}
        return RunResult(
            status=self.status, ended_by=self.ended_by, rounds=self.world.round, seed=self.seed, arm=self.arm,
            inputs=self.inputs, outputs=outputs, metrics=dict(self.world.metrics),
            series={k: list(v) for k, v in self.world.series.items()}, winner=end.get("winner"),
            error=self.error, output_issues=issues, stats=self.stats.to_dict(),
            events=[e.to_dict() for e in self.world.log],
        )

    def preview(self, entity_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
        """What the agent would receive on its next turn: brief, update and tools. Changes nothing.

        Between rounds this plays the start of the next round on a copy (scheduled effects,
        start events, physics), so the preview shows the turn exactly as the agent will get it.
        """
        if self.world.entity(entity_id) is None:
            raise KeyError(f"no entity '{entity_id}'")
        if not self.finished and self.world.stage is None:
            probe = Env.restore(self.contract, self.snapshot(), parallel=1)
            probe._participants_spec = dict(getattr(self, "_participants_spec", {}))
            probe._begin_round()
            return probe._preview_now(entity_id, stage)
        return self._preview_now(entity_id, stage)

    def _preview_now(self, entity_id: str, stage: Optional[str]) -> Dict[str, Any]:
        actor = self.world.entity(entity_id)
        if actor is None:
            raise KeyError(f"no entity '{entity_id}'")
        stages = self.contract.stage_list()
        if stage:
            spec = next((s for s in stages if s.name == stage), None)
        else:
            spec = next((s for s in stages if stage_actions(self.contract, s, actor.entity_type)), stages[0])
        if spec is None:
            raise KeyError(f"no stage '{stage}' (stages: {', '.join(s.name for s in stages)})")
        reason = "Everyone chooses at the same time." if spec.turns == "simultaneous" else "It is your turn."
        if spec.when is not None and not truthy(compile_expr(spec.when)(self.world.scope())):
            reason = f"(Preview only: stage {spec.name} does not run this round.)"
        elif actor not in self._eligible(spec):
            reason = f"(Preview only: {actor.name} would not be woken in {spec.name} now.)"
        turn = _Turn(self, actor, spec, reason, spec.turns == "simultaneous", peek=True)
        tools = turn.tools()
        return {"brief": turn.brief, "update": turn.update, "tools": [t.to_dict() for t in tools],
                "tokens": {"brief": len(turn.brief) // 4, "update": len(turn.update) // 4,
                           "tools": len(json.dumps([t.to_anthropic() for t in tools])) // 4}}

    # -- round -----------------------------------------------------------------------

    def _begin_round(self) -> bool:
        """Start the next round: scheduled effects, start events, physics. False if the run ended."""
        world = self.world
        if self.status == "ready":
            self.status = "running"
        world.round += 1
        world.stage = None
        self._used_round.clear()
        self._run_scheduled()
        self._run_events("start")
        self._check_end()
        if self._ended():
            self._finish()
            return False
        with self._lock:
            world.step_physics()
            world.journal.clear()
        return True

    def _round(self) -> None:
        world = self.world
        if not self._begin_round():
            return
        for stage in self.contract.stage_list():
            if self._stopped():
                return
            self._run_stage(stage)
            self._check_end()
            if self._ended():
                return self._finish()
        world.stage = None
        self._run_events("end")
        sample_metrics(self.contract, world)
        self._check_invariants("round")
        self._check_end()
        self._flush_events()
        if self._ended():
            return self._finish()
        if world.round >= world.rounds:
            self.ended_by = "rounds"
            self.status = "completed"
            self._final_event()

    def _stopped(self) -> bool:
        if self._stop is not None and self._stop(self):
            self.status = "stopped"
            return True
        return False

    def _ended(self) -> bool:
        return self.world.end_request is not None

    def _finish(self) -> None:
        world = self.world
        if world.stage is not None:
            world.stage = None
        if not world.series or len(next(iter(world.series.values()), [])) < world.round:
            sample_metrics(self.contract, world)
        end = world.end_request or {}
        self.ended_by = end.get("name") or "end"
        self.status = "ended"
        self._final_event()

    def _final_event(self) -> None:
        end = self.world.end_request or {}
        text = end.get("text") or (f"The run ended: {self.ended_by}." if self.ended_by != "rounds" else "Time is up.")
        self.world.emit("end", text, data={"ended_by": self.ended_by, "winner": end.get("winner")})
        self.world.journal.clear()
        self._flush_events()

    def _run_scheduled(self) -> None:
        import heapq

        world = self.world
        while world.scheduled and world.scheduled[0][0] <= world.round:
            _, _, item = heapq.heappop(world.scheduled)
            self._atomic(item["effects"], world.thaw(item["vars"]), item["path"])

    def _run_events(self, phase: str) -> None:
        world = self.world
        for index, event in enumerate(self.contract.events):
            if event.phase != phase:
                continue
            path = f"events[{index}]"
            if event.arms is not None and self.arm not in event.arms:
                continue
            if event.once and index in self._fired_once:
                continue
            if not self._due(event, path):
                continue
            if event.once:
                self._fired_once.add(index)
            vars: Dict[str, Any] = {}
            if event.each is not None:
                item_name = event.as_ or "it"
                try:
                    items = world.entities_of(event.each) if event.each in self.contract.types else \
                        compile_expr(event.each)(world.scope())
                    for position, item in enumerate(items or []):
                        inner = {item_name: item, "i": position}
                        if event.where is not None and not truthy(compile_expr(event.where)(world.scope(**inner))):
                            continue
                        self._atomic(event.do, inner, f"{path}.do")
                except ExprError as exc:
                    raise RunError(str(exc), path) from None
            else:
                self._atomic(event.do, vars, f"{path}.do")
            if event.say:
                try:
                    text = compile_template(event.say, None).render(world.scope())
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.say") from None
                if text.strip():
                    world.emit("news", text, data={"event": event.name or index})
                world.journal.clear()
            if self._ended():
                return

    def _due(self, event: Any, path: str) -> bool:
        world = self.world
        scope = world.scope()
        try:
            if event.at is not None:
                at = compile_expr(event.at)(scope) if isinstance(event.at, str) else event.at
                rounds = at if isinstance(at, list) else [at]
                if world.round not in rounds:
                    return False
            if event.every is not None and (world.round - 1) % event.every != 0:
                return False
            if event.when is not None and not truthy(compile_expr(event.when)(scope)):
                return False
            if event.chance is not None:
                p = compile_expr(event.chance)(scope) if isinstance(event.chance, str) else event.chance
                if world.rng.random() >= p:
                    return False
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        return True

    def _atomic(self, effects: List[Any], vars: Dict[str, Any], path: str) -> bool:
        if not effects:
            return True
        with self._lock:
            mark = self.world.journal.mark()
            try:
                self.effects.run(effects, dict(vars), path)
            except Abort:
                self.world.journal.rollback(mark)
                return False
            except RunError:
                self.world.journal.rollback(mark)
                raise
            self._after_commit(path)
        return True

    def _after_commit(self, path: str) -> None:
        self._check_invariants(path)
        self.world.journal.clear()

    # -- stages & turns ------------------------------------------------------------------

    def _run_stage(self, stage: StageSpec) -> None:
        world = self.world
        path = f"stages.{stage.name}"
        if stage.when is not None:
            try:
                if not truthy(compile_expr(stage.when)(world.scope())):
                    return
            except ExprError as exc:
                raise RunError(str(exc), f"{path}.when") from None
        world.stage = stage.name
        self._atomic(stage.on_enter, {}, f"{path}.on_enter")
        if self._ended():
            return
        passes = stage.passes or (10 if stage.until else 1)
        for pass_index in range(passes):
            agents = self._eligible(stage)
            if stage.turns == "simultaneous":
                self._simultaneous(stage, agents, pass_index)
            else:
                self._sequential(stage, agents, pass_index)
            if self._ended() or self.status == "stopped":
                return
            if stage.until is not None:
                try:
                    if truthy(compile_expr(stage.until)(world.scope())):
                        break
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.until") from None
        self._atomic(stage.on_exit, {}, f"{path}.on_exit")

    def _eligible(self, stage: StageSpec) -> List[Entity]:
        world = self.world
        agent_types = set(self.contract.agent_types())  # includes types that inherit `agent`
        agents = [e for e in world.entities.values() if e.alive and e.entity_type in agent_types
                  and stage_actions(self.contract, stage, e.entity_type)]
        path = f"stages.{stage.name}"
        try:
            if stage.who is not None:
                who = compile_expr(stage.who)
                agents = [a for i, a in enumerate(agents) if truthy(who(world.scope(it=a, i=i)))]
            if stage.order == "random":
                world.rng.shuffle(agents)
            elif stage.order != "seat":
                key = compile_expr(stage.order)
                keyed = [(key(world.scope(it=a, i=i)), i, a) for i, a in enumerate(agents)]
                keyed.sort(key=lambda t: (t[0], t[1]))
                agents = [a for _, _, a in keyed]
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        except TypeError:
            raise RunError("`order` must give comparable values (numbers or text)", f"{path}.order") from None
        return agents

    def _reason(self, actor: Entity, stage: StageSpec, pass_index: int) -> Optional[str]:
        requested = self.world.wake_requests.pop(actor.id, None)
        memory = self._memory(actor.id)
        if stage.quiet == "skip" and requested is None and pass_index > 0:
            if not self.perception.news(actor, memory.cursor, 1)[0]:
                return None
        if requested:
            return requested
        if stage.turns == "simultaneous":
            return "Everyone chooses at the same time."
        return "It is your turn." if pass_index == 0 else "Your turn again."

    def _sequential(self, stage: StageSpec, agents: List[Entity], pass_index: int) -> None:
        for actor in agents:
            if not actor.alive or self._ended() or self._stopped():
                return
            reason = self._reason(actor, stage, pass_index)
            if reason is None:
                continue
            turn = _Turn(self, actor, stage, reason, staged=False)
            self._drive(turn)
            if stage.on_idle and turn.stats.actions == 0 and actor.alive:
                self._atomic(stage.on_idle, {"actor": actor}, f"stages.{stage.name}.on_idle")
            memory = self._memory(actor.id)
            memory.cursor = self.world.log[-1].seq if self.world.log else 0
            memory.turns += 1
            self._flush_events()

    def _simultaneous(self, stage: StageSpec, agents: List[Entity], pass_index: int) -> None:
        turns: List[_Turn] = []
        for actor in agents:
            reason = self._reason(actor, stage, pass_index)
            if reason is None:
                continue
            turns.append(_Turn(self, actor, stage, reason, staged=True))
        cursor = self.world.log[-1].seq if self.world.log else 0
        for turn in turns:
            memory = self._memory(turn.actor.id)
            memory.cursor = cursor
            memory.turns += 1
        concurrent = [t for t in turns if getattr(self._participant(t.actor), "concurrent", True)]
        if self.parallel > 1 and len(concurrent) > 1:
            with ThreadPoolExecutor(max_workers=min(self.parallel, len(concurrent))) as pool:
                list(pool.map(self._drive, concurrent))
            for turn in turns:
                if turn not in concurrent:
                    self._drive(turn)
        else:
            for turn in turns:
                self._drive(turn)
        for turn in turns:
            for name, args in turn.intents:
                if self._ended():
                    return
                self._commit_intent(turn, name, args)
            if stage.on_idle and not turn.intents and turn.actor.alive and not self._ended():
                self._atomic(stage.on_idle, {"actor": turn.actor}, f"stages.{stage.name}.on_idle")
        self._flush_events()

    def _commit_intent(self, turn: _Turn, name: str, args: Dict[str, Any]) -> None:
        actor, world = turn.actor, self.world
        blocked = self.actions.blocked(actor, name, {}, {}) if actor.alive else "you are no longer active"
        params, problem = ({}, blocked) if blocked else self.actions.validate(actor, name, args)
        verb = name.replace("_", " ")
        with self._lock:
            if problem:
                world.emit("outcome", f"Your {verb} did not happen: {str(problem).rstrip('.')}.",
                           actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
                world.journal.clear()
                self.stats.rejected_actions += 1
                return
            outcome = self.actions.apply(actor, name, params)
            text = outcome.text if outcome.ok else f"Your {verb} failed: {outcome.text}"
            world.emit("outcome", text, actor=actor.id, to=(actor.id,), data={"action": name, "ok": outcome.ok})
            if outcome.ok:
                self.stats.actions += 1
                self._after_commit(f"actions.{name}")
            else:
                self.stats.rejected_actions += 1
                world.journal.clear()

    def _drive(self, turn: _Turn) -> None:
        participant = self._participant(turn.actor)
        world = self.world
        world.use_turn_rng(self.seeds.rng("turn", world.round, turn.number))
        world.use_turn_pending(turn.pending)
        try:
            participant(Wake(turn))
        except (RunError, ExprError):
            raise
        except Exception as exc:
            raise RunError(f"participant for {turn.actor.id} raised {type(exc).__name__}: {exc}",
                           f"participant:{turn.actor.id}") from exc
        finally:
            world.use_turn_rng(None)
            world.use_turn_pending(None)
            turn.done = True
            if turn.stats.actions == 0 and not turn.intents:
                turn.stats.idle_turns += 1
            with self._lock:
                self.stats.add(turn.stats)

    # -- participants ----------------------------------------------------------------------

    def _bind(self, participants: Any) -> None:
        if participants is None:
            return
        if callable(participants) or isinstance(participants, str):
            participants = {"*": participants}
        if not isinstance(participants, Mapping):
            raise TypeError("participants must be a callable, a string, or a mapping")
        known = set(self.contract.types) | set(self.world.entities) | {"*"}
        for key in participants:
            if key not in known:
                raise ValueError(f"participants key '{key}' is not an entity id, a type, or '*'")
        self._participants_spec = dict(participants)
        self._participants.clear()

    def _participant(self, actor: Entity) -> Participant:
        cached = self._participants.get(actor.id)
        if cached is not None:
            return cached
        spec = getattr(self, "_participants_spec", {})
        lineage = list(reversed(self.contract.lineage(actor.entity_type)))  # most specific type first
        value = spec.get(actor.id)
        if value is None:
            value = next((spec[kind] for kind in lineage if kind in spec), spec.get("*"))
        if value is None:
            value = next((self.contract.types[kind].policy for kind in lineage if self.contract.types[kind].policy),
                         None) or "random"
        participant = resolve_participant(value, self.contract, self.seeds.derive("participant"))
        self._participants[actor.id] = participant
        return participant

    # -- checks --------------------------------------------------------------------------------

    def _inspect_rule(self, type_name: str) -> Any:
        """The inspect rule for a type, inherited through `extends`."""
        for kind in reversed(self.contract.lineage(type_name)):
            if "inspect" in self.contract.types[kind].model_fields_set:
                return self.contract.types[kind].inspect
        return True

    def _check_invariants(self, path: str) -> None:
        scope = self.world.scope()
        for index, invariant in enumerate(self.contract.invariants):
            try:
                holds = truthy(compile_expr(invariant.expr)(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"invariants[{index}]") from None
            if not holds:
                why = f" ({invariant.why})" if invariant.why else ""
                raise InvariantViolation(f"invariant `{invariant.expr}` no longer holds after {path}{why}",
                                         f"invariants[{index}]")

    def _check_end(self) -> None:
        world = self.world
        if world.end_request is not None or world.round == 0:
            return
        scope = world.scope()
        for index, end in enumerate(self.contract.end):
            path = f"end[{index}]"
            try:
                if not truthy(compile_expr(end.when)(scope)):
                    continue
                winner = _plain(compile_expr(end.winner)(scope)) if end.winner else None
                text = compile_template(end.say, None).render(scope) if end.say else ""
            except ExprError as exc:
                raise RunError(str(exc), path) from None
            world.request_end(end.name or f"end_{index}", winner, text)
            return

    # -- helpers --------------------------------------------------------------------------------

    def _memory(self, entity_id: str) -> _Memory:
        memory = self._memories.get(entity_id)
        if memory is None:
            memory = self._memories[entity_id] = _Memory()
        return memory

    def _brief(self, actor: Entity) -> str:
        brief = self._briefs.get(actor.id)
        if brief is None:
            brief = self._briefs[actor.id] = self.perception.brief(actor)
        return brief

    def _flush_events(self) -> None:
        if self._on_event is None:
            self._emitted = len(self.world.log)
            return
        while self._emitted < len(self.world.log):
            event = self.world.log[self._emitted]
            self._emitted += 1
            self._on_event(event.to_dict())

    # -- snapshots ----------------------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Everything needed to continue this run later, as JSON-safe data (between rounds)."""
        if self.world.stage is not None:
            raise SnapshotError("snapshots are taken between rounds, not during a stage")
        w = self.world
        state = w.rng.getstate()
        return {
            "fg_env_snapshot": SNAPSHOT_VERSION,
            "contract": contract_hash(self.contract),
            "seed": self.seed, "arm": self.arm, "inputs": self.inputs,
            "status": self.status, "ended_by": self.ended_by, "error": self.error,
            "round": w.round, "rounds": w.rounds,
            "entities": [{"id": e.id, "type": e.entity_type, "name": e.name, "props": _encode(e.properties),
                          "alive": e.alive, "at": e.location_id} for e in w.entities.values()],
            "entity_briefs": w.entity_briefs,
            "props": _encode(w.props),
            "links": {kind: [[a, b, v] for (a, b), v in edges.items()] for kind, edges in w.links.items()},
            "records": {name: [_encode(dict(row)) for row in rows] for name, rows in w.records_store.items()},
            "record_seq": w._record_seq,
            "log": [e.to_dict() for e in w.log], "seq": w._seq,
            "physics": w.physics.to_dict() if w.physics else None,
            "metrics": w.metrics, "series": w.series,
            "scheduled": [[due, seq, item] for due, seq, item in w.scheduled],
            "counters": w.counters, "end_request": w.end_request,
            "fired_once": sorted(self._fired_once),
            "memory": {k: {"cursor": m.cursor, "views": m.views, "turns": m.turns} for k, m in self._memories.items()},
            "rng": [state[0], list(state[1]), state[2]],
            "stats": self.stats.to_dict(),
        }

    @classmethod
    def restore(cls, contract: Contract, snapshot: Mapping[str, Any], parallel: int = 8) -> "Env":
        if snapshot.get("fg_env_snapshot") != SNAPSHOT_VERSION:
            raise SnapshotError(f"unsupported snapshot version {snapshot.get('fg_env_snapshot')!r}")
        if snapshot.get("contract") != contract_hash(contract):
            raise SnapshotError("the snapshot was taken with a different contract")
        env = cls(contract, dict(snapshot["inputs"]), int(snapshot["seed"]), snapshot.get("arm"), parallel)
        w = env.world
        from ..physics import PhysicsModel

        w.entities = {}
        for row in snapshot["entities"]:
            w.entities[row["id"]] = Entity(id=row["id"], name=row["name"], entity_type=row["type"],
                                           properties=_decode(row["props"]), location_id=row.get("at"),
                                           alive=row["alive"])
        w.props = _decode(snapshot["props"])
        w.entity_briefs = dict(snapshot.get("entity_briefs", {}))
        w.links = {kind: {(a, b): v for a, b, v in edges} for kind, edges in snapshot["links"].items()}
        w.rebuild_adjacency()
        w.records_store = {}
        w.entry_by_seq = {}
        for name, rows in snapshot["records"].items():
            entries = []
            for row in rows:
                entry = Entry(_decode(row))
                entry.world = w
                entries.append(entry)
                w.entry_by_seq[entry["seq"]] = entry
            w.records_store[name] = entries
        w._record_seq = snapshot["record_seq"]
        w.log = [LogEvent(e["seq"], e["round"], e["kind"], e.get("text", ""), e.get("actor"),
                          tuple(e["to"]) if e.get("to") is not None else None, e.get("data", {}), e.get("stage"))
                 for e in snapshot["log"]]
        w._seq = snapshot["seq"]
        if snapshot.get("physics") and w.physics is not None:
            restored = PhysicsModel.from_dict(snapshot["physics"])
            w.physics.params, w.physics.time = restored.params, restored.time
            for name, var in restored.variables.items():
                w.physics.variables[name].value = var.value
        w.metrics = dict(snapshot["metrics"])
        w.series = {k: list(v) for k, v in snapshot["series"].items()}
        w.scheduled = [(due, seq, item) for due, seq, item in snapshot["scheduled"]]
        w.counters = dict(snapshot["counters"])
        w.end_request = snapshot.get("end_request")
        w.round, w.rounds = snapshot["round"], snapshot["rounds"]
        state = snapshot["rng"]
        w.rng.setstate((state[0], tuple(state[1]), state[2]))
        env._fired_once = set(snapshot["fired_once"])
        for key, m in snapshot["memory"].items():
            memory = env._memory(key)
            memory.cursor, memory.views, memory.turns = m["cursor"], dict(m["views"]), m["turns"]
        env.status = snapshot["status"] if snapshot["status"] != "stopped" else "running"
        env.ended_by, env.error = snapshot.get("ended_by"), snapshot.get("error")
        for name in Stats.__dataclass_fields__:
            setattr(env.stats, name, snapshot["stats"].get(name, 0))
        w.journal.clear()
        env._emitted = len(w.log)
        return env
