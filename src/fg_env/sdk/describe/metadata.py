"""Game metadata derived from a contract: what an algorithm or evaluator may assume, each with its evidence.

What the contract does not settle is ``None`` (for numbers) or ``"unknown"`` (for classes), and the evidence
says why. Nothing is guessed from names or from sample runs.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple, Union

from ..contract import Contract, ParamSpec
from . import walk

__all__ = ["game_metadata", "MAX_EVIDENCE"]

#: Most evidence lines kept per property (the rest are counted).
MAX_EVIDENCE = 12
_INPUT_REF = re.compile(r"\s*\$inputs\.([A-Za-z_][A-Za-z0-9_]*)\s*")
_MEASURE_REF = re.compile(r"\$(?:metrics|series)\.([A-Za-z_][A-Za-z0-9_]*)")
_RANDOM_GRAPHS = frozenset({"random", "small_world", "scale_free", "blocks"})
_MARKETS = frozenset({"market", "auction", "order_book", "posted_market", "prediction_market"})
_TALK = frozenset({"social.channels", "decision.deliberation", "channels", "deliberation"})
_BOARDS = frozenset({"game.board", "board"})
_CARDS = frozenset({"game.cards", "cards"})
_ROLES = frozenset({"groups.roles", "roles"})

Choices = Union[int, str, None]


def _capped(lines: Iterable[str]) -> List[str]:
    unique = list(dict.fromkeys(lines))
    if len(unique) <= MAX_EVIDENCE:
        return unique
    return unique[:MAX_EVIDENCE] + [f"… and {len(unique) - MAX_EVIDENCE} more"]


class _Scan:
    """One pass over the contract's texts and objects, shared by every derivation."""

    def __init__(self, contract: Contract):
        data = walk.dumped(contract)
        self.nodes = [(path, node) for path, node in walk.effect_nodes(data)]
        self.effects = [(path, node) for path, node in self.nodes if walk.in_effects(path)]
        posts = [path for path, node in self.effects if "post" in node]
        self.texts: List[Tuple[str, str, FrozenSet[str]]] = []
        spectators = tuple(f"views.{name}." for name, view in contract.views.items() if _spectator(view))
        for path, text in walk.texts(data):
            where = set(walk.roles(path))
            if any(_record_field(path, prefix) for prefix in posts):
                where.add("shown")  # a post's fields become an entry agents read
            if path.startswith(spectators):
                where.clear()  # rendered for reports and UIs, never for an agent
            self.texts.append((path, text, frozenset(where)))
        shown = [text for _, text, where in self.texts if "shown" in where]
        rules = [text for _, text, where in self.texts if "rules" in where]
        measured = {name for text in shown for name in _MEASURE_REF.findall(text) if name in contract.metrics}
        called = {name for text in shown for name in walk.calls(text) if name in contract.defs}
        shown += [contract.metrics[name].expr for name in measured] + [contract.defs[name].expr for name in called]
        self.shown_world: Set[str] = set().union(*(walk.world_reads(text) for text in shown))
        self.rule_world: Set[str] = set().union(*(walk.world_reads(text) for text in rules))
        self.shows_physics = any("$physics." in text for text in shown)
        self.created = [(path, node["create"]) for path, node in self.effects if "create" in node]


def _spectator(view: Any) -> bool:
    return "spectator" in (view.for_ if isinstance(view.for_, list) else [view.for_])


def _lossy(node: Mapping[str, Any]) -> bool:
    return node.get("drop") not in (None, 0, False) and bool({"post", "emit", "wake"} & set(node))


def _record_field(path: str, prefix: str) -> bool:
    if not path.startswith(prefix + "."):
        return False
    return re.split(r"[.\[]", path[len(prefix) + 1:])[0] not in ("post", "to", "author")


def game_metadata(contract: Contract, probe: Any = None, probe_error: Optional[str] = None) -> Dict[str, Any]:
    """Metadata for ``contract``; ``probe`` is an :class:`Env` built from it (for counts), or ``None`` with ``probe_error``."""
    scan = _Scan(contract)
    dynamics, dynamics_evidence = _dynamics(contract)
    chance, during, chance_evidence = _chance(contract, scan)
    information, information_evidence = _information(contract, scan)
    count, low, high, by_type, players_evidence = _players(contract, scan, probe)
    if probe is None and probe_error:
        players_evidence.insert(0, f"the contract could not be built with these inputs: {probe_error}")
    fixed = low if low is not None and low == high else None
    length, length_evidence = _length(contract, scan, probe, fixed)
    space, space_evidence = _action_space(contract, scan, probe)
    evidence = {"dynamics": dynamics_evidence, "chance_mode": chance_evidence, "information": information_evidence,
                "utility": ["the contract declares no per-player returns, so zero-sum or constant-sum cannot be established"],
                "players": players_evidence, "max_game_length": length_evidence, "action_space": space_evidence}
    return {"name": contract.name, "dynamics": dynamics, "chance_mode": chance, "chance_during": during,
            "information": information, "utility": "unknown", "num_players": count, "min_players": low,
            "max_players": high, "players_by_type": by_type, "max_game_length": length, "action_space": space,
            "observations": _observations(contract), "concepts": _concepts(contract, scan),
            "external_data": [{"feed": name, "host": feed.host, "into": feed.into, "every": feed.every}
                              for name, feed in contract.feeds.items()],
            "evidence": {key: _capped(lines) for key, lines in evidence.items()}}


def _acting(contract: Contract) -> List[Any]:
    return [stage for stage in contract.stage_list() if stage.actions != []]


def _dynamics(contract: Contract) -> Tuple[str, List[str]]:
    if not contract.agent_types():
        return "none", ["no type takes turns"]
    acting = _acting(contract)
    if not acting:
        return "none", ["no stage offers actions"]
    kinds = list(dict.fromkeys(stage.turns for stage in acting))
    return (kinds[0] if len(kinds) == 1 else "mixed"), [_stage_note(s) for s in acting]


def _stage_note(stage: Any) -> str:
    notes = [f"stage {stage.name}: {stage.turns} turns"]
    if stage.atomic or stage.valid:
        notes.append("atomic (a turn's actions stand or fall together)")
    if stage.time_limit is not None:
        notes.append(f"time limit {stage.time_limit} s")
    return ", ".join(notes)


def _chance(contract: Contract, scan: _Scan) -> Tuple[str, List[str], List[str]]:
    functions, ops = walk.random_functions(), walk.random_ops()
    setup: List[str] = []
    play: List[str] = []
    for path, text, where in scan.texts:
        names = sorted(walk.calls(text) & functions)
        if names and where:
            (setup if where == {"setup"} else play).append(f"{path} calls " + ", ".join(f"${n}" for n in names))
    for path, node in scan.effects:
        for op in sorted(walk.ops_in(node) & ops):
            play.append(f"{path} uses the {op} op, which draws at random")
        if _lossy(node):
            play.append(f"{path} may lose the message (drop)")
    play += [f"actions.{name}.chance is {spec.chance}" for name, spec in contract.actions.items() if spec.chance is not None]
    play += [f"events[{i}].chance is {e.chance}" for i, e in enumerate(contract.events) if e.chance is not None]
    play += [f"stage {s.name} wakes agents in random order" for s in contract.stage_list() if s.order == "random"]
    if contract.physics is not None:
        play += [f"physics.vars.{name}.noise is a random term" for name, var in contract.physics.vars.items() if var.noise]
        play += [f"physics.per.{kind}.vars.{name}.noise is a random term" for kind, dynamics in contract.physics.per.items()
                 for name, var in dynamics.vars.items() if var.noise]
    for i, link in enumerate(contract.links):
        if link.graph in _RANDOM_GRAPHS or (link.graph is not None and link.p is not None):
            setup.append(f"links[{i}] draws a {link.graph} network")
    for i, group in enumerate(contract.population):
        if group.weight or (group.from_ is not None and group.count is not None):
            setup.append(f"population[{i}] samples rows")
        if group.mix and not group.quota:
            setup.append(f"population[{i}] draws each member's archetype")
    during = [label for label, found in (("setup", setup), ("play", play)) if found]
    return ("sampled" if during else "deterministic"), during, setup + play


def _information(contract: Contract, scan: _Scan) -> Tuple[str, List[str]]:
    hiding: List[str] = []
    private = [f"types.{name}.props.{prop}" for name, spec in contract.types.items()
               for prop, p in spec.props.items() if p.private]
    private += [f"world.{prop}" for prop, p in contract.world.items() if p.private]
    if private:
        hiding.append("private props: " + ", ".join(private))
    for name, spec in contract.types.items():
        if isinstance(spec.inspect, str):
            hiding.append(f"types.{name}.inspect limits who may inspect it: `{spec.inspect}`")
    unannounced = [f"actions.{name}" for name, a in contract.actions.items() if a.private]
    if unannounced:
        hiding.append("private actions (others are not told they happened): " + ", ".join(unannounced))
    hiding += [f"records.{name} is readable only when `{r.visible}`" for name, r in contract.records.items()
               if r.visible.strip() != "all"]
    hiding += [f"{path} reaches only `{node['to']}`" for path, node in scan.effects
               if ("post" in node or "emit" in node) and node.get("to") not in (None, "", [])]
    hiding += [f"{path} can lose messages on the way (drop)" for path, node in scan.effects if _lossy(node)]
    hiding += [f"entities.{eid}.brief is private to that entity" for eid, e in contract.entities.items() if e.brief]
    for i, group in enumerate(contract.population):
        if group.brief or any(m.brief for m in group.mix) or any(m.brief for m in group.members):
            hiding.append(f"population[{i}] gives members private briefs")
    hiding += [f"stage {s.name}: agents choose without seeing each other's choices" for s in _acting(contract)
               if s.turns == "simultaneous"]
    if hiding:
        return "imperfect", hiding
    unknown = [f"entities of type {name} cannot be inspected, and the scan does not check that views show their state"
               for name, spec in contract.types.items() if spec.inspect is False]
    unseen = sorted(scan.rule_world - scan.shown_world)
    if unseen:
        unknown.append("world props the rules read but no view, brief or message shows: " + ", ".join(unseen))
    if contract.physics is not None and not scan.shows_physics:
        unknown.append("physics variables drive the world but no view or brief reads $physics")
    if unknown:
        return "unknown", unknown
    return "perfect", ["no private props, hidden records, targeted messages, private briefs, viewer-dependent "
                       "inspection or simultaneous choices, and every world prop the rules read is shown"]


def _players(contract: Contract, scan: _Scan, probe: Any) -> Tuple[Optional[int], Optional[int], Optional[int],
                                                                    Dict[str, int], List[str]]:
    evidence: List[str] = []
    by_type: Dict[str, int] = {}
    count: Optional[int] = None
    if probe is not None:
        for entity in probe.world.entities.values():
            if probe.contract.is_agent(entity.entity_type):
                by_type[entity.entity_type] = by_type.get(entity.entity_type, 0) + 1
        count = sum(by_type.values())
    fixed = sum(1 for e in contract.entities.values() if contract.is_agent(e.type))
    low: Optional[int] = fixed
    high: Optional[int] = fixed
    for i, group in enumerate(contract.population):
        path = f"population[{i}]"
        if any(contract.is_agent(m.type) for m in group.members):
            low = high = None
            evidence.append(f"{path}.members generates agents inside each entity")
        if not contract.is_agent(group.type):
            continue
        spec = contract.inputs.get(_INPUT_REF.fullmatch(group.count).group(1), None) \
            if isinstance(group.count, str) and _INPUT_REF.fullmatch(group.count) else None
        if isinstance(group.count, int) and not isinstance(group.count, bool):
            low, high = _add(low, group.count), _add(high, group.count)
        elif spec is not None and spec.min is not None and spec.max is not None:
            low, high = _add(low, int(math.ceil(spec.min))), _add(high, int(math.floor(spec.max)))
            evidence.append(f"{path}.count follows an input between {spec.min:g} and {spec.max:g}")
        else:
            low = high = None
            evidence.append(f"{path} makes one agent per data row" if group.count is None
                            else f"{path}.count is `{group.count}`")
    created = [path for path, kind in scan.created if not isinstance(kind, str) or kind not in contract.types
               or contract.is_agent(kind)]
    if created:
        high = None
        evidence.append(f"{created[0]} can create agents during play")
    if count is not None and low is not None and high is not None and not low <= count <= high:
        low = high = None
        evidence.append("the agents at the start differ from the declared entities and populations")
    return count, low, high, by_type, evidence


def _add(total: Optional[int], amount: int) -> Optional[int]:
    return None if total is None else total + amount


def _length(contract: Contract, scan: _Scan, probe: Any, players: Optional[int]) -> Tuple[Dict[str, Any], List[str]]:
    clock = contract.clock
    evidence: List[str] = []
    rounds: Optional[int] = None
    time: Optional[float] = None
    if clock.mode == "continuous":
        time = float(clock.horizon) if isinstance(clock.horizon, (int, float)) else \
            (probe.world.horizon if probe is not None else None)
        evidence.append(f"continuous clock: the run completes at time {time}" if time is not None
                        else f"continuous clock with horizon `{clock.horizon}`")
    if clock.mode != "continuous" or "rounds" in clock.model_fields_set:
        if isinstance(clock.rounds, int):
            rounds = clock.rounds
        elif probe is not None:
            rounds = probe.world.rounds
            evidence.append(f"clock.rounds is `{clock.rounds}`: {rounds} with these inputs")
        else:
            evidence.append(f"clock.rounds is `{clock.rounds}` and the contract could not be built")
    wakes = [path for path, node in scan.effects if "wake" in node]
    acting = _acting(contract)
    decisions: Optional[int] = None
    if rounds is None:
        pass
    elif any(stage.turns == "scheduled" for stage in acting):
        evidence.append("scheduled turns come as often as action durations allow")
    elif wakes:
        evidence.append(f"{wakes[0]} wakes agents for extra turns")
    elif any(stage.valid for stage in acting):
        evidence.append(f"stage {next(s.name for s in acting if s.valid)} replays a turn that breaks `valid`")
    elif players is None:
        evidence.append("the number of players is not fixed")
    else:
        per_round = sum((s.passes or (10 if s.until else 1)) * s.max_actions for s in acting) * players
        decisions = rounds * per_round
        evidence.append(f"at most {rounds} rounds × {per_round} decisions a round "
                        "(passes × actions per turn, over the stages, × players)")
    return {"rounds": rounds, "time": time, "decisions": decisions}, evidence


def _action_space(contract: Contract, scan: _Scan, probe: Any) -> Tuple[Dict[str, Any], List[str]]:
    created: Set[str] = set()
    dynamic = False
    for _, kind in scan.created:
        if isinstance(kind, str) and kind in contract.types:
            created.add(kind)
        else:
            dynamic = True
    counts: Dict[str, int] = {}
    if probe is not None:
        for entity in probe.world.entities.values():
            for kind in contract.lineage(entity.entity_type):
                counts[kind] = counts.get(kind, 0) + 1
    evidence: List[str] = []
    per_action: Dict[str, Union[int, str]] = {}
    for name, action in contract.actions.items():
        size, kind = 1, "finite"
        for param, spec in action.params.items():
            choices, why = _choices(contract, spec, counts, created, dynamic, probe is not None)
            if isinstance(choices, int):
                size *= choices
                continue
            evidence.append(f"actions.{name}.params.{param} is {why}")
            kind = "parametric" if choices == "parametric" or kind == "parametric" else "unknown"
        per_action[name] = size if kind == "finite" else kind
    kinds = {value if isinstance(value, str) else "finite" for value in per_action.values()}
    overall = "parametric" if "parametric" in kinds else "unknown" if "unknown" in kinds else "finite"
    total = sum(v for v in per_action.values() if isinstance(v, int)) if overall == "finite" else None
    if overall == "finite" and per_action:
        evidence.insert(0, "counts every declared choice; `when` and `where` rules offer a subset at any moment")
    return {"kind": overall, "size": total, "per_action": per_action}, evidence


def _choices(contract: Contract, spec: ParamSpec, counts: Mapping[str, int], created: Set[str], dynamic: bool,
             probed: bool) -> Tuple[Choices, str]:
    if spec.type == "bool":
        return 2, ""
    if spec.type == "enum":
        return (len(spec.values), "") if isinstance(spec.values, list) else (None, f"an enum whose values come from `{spec.values}`")
    if spec.type == "int":
        bounds = (spec.min, spec.max)
        if all(isinstance(b, (int, float)) and not isinstance(b, bool) for b in bounds):
            return max(0, int(math.floor(float(spec.max))) - int(math.ceil(float(spec.min))) + 1), ""  # type: ignore[arg-type]
        if any(isinstance(b, str) for b in bounds):
            return None, "a whole number whose bounds depend on the state"
        return "parametric", "a whole number without both bounds"
    if spec.type == "entity":
        if not probed:
            return None, "an entity, and the contract could not be built to count them"
        if dynamic or any(contract.is_a(kind, spec.of or "") for kind in created):
            return None, f"an entity of type {spec.of}, which can be created during play"
        return counts.get(spec.of or "", 0), ""
    what = {"number": "a number", "text": "free text", "list": "a list of items (combinations)"}
    return "parametric", what.get(spec.type, spec.type)


def _observations(contract: Contract) -> Dict[str, Any]:
    agents = contract.agent_types()
    views: Dict[str, List[str]] = {kind: [] for kind in agents}
    for name, view in contract.views.items():
        audience = view.for_ if isinstance(view.for_, list) else [view.for_]
        for kind in agents:
            if "all" in audience or any(contract.is_a(kind, a) for a in audience):
                views[kind].append(name + (" (on request)" if view.look else ""))
    records = {name: "everyone" if r.visible.strip() == "all" else f"only when `{r.visible}`"
               for name, r in contract.records.items()}
    inspect: Dict[str, str] = {}
    for kind in contract.types:
        rule: Any = True
        for ancestor in reversed(contract.lineage(kind)):
            if "inspect" in contract.types[ancestor].model_fields_set:
                rule = contract.types[ancestor].inspect
                break
        inspect[kind] = "everyone" if rule is True else "no one" if rule is False else f"only when `{rule}`"
    return {"text": True, "struct": False, "tensor": False, "views": views, "records": records, "inspect": inspect,
            "spectator": [name for name, view in contract.views.items() if _spectator(view)]}


def _concepts(contract: Contract, scan: _Scan) -> List[str]:
    kinds = walk.mechanism_kinds(contract)
    space = contract.space
    found = {
        "board": (space is not None and space.grid is not None) or bool(kinds & _BOARDS),
        "graph_space": space is not None and space.graph is not None,
        "plane": space is not None and space.plane is not None,
        "cards": bool(kinds & _CARDS),
        "hidden_roles": bool(kinds & _ROLES),
        "communication": bool(kinds & _TALK) or any("post" in node and path.startswith("actions.") for path, node in scan.effects),
        "markets": bool(kinds & _MARKETS),
        "networks": bool(contract.relations),
        "population": bool(contract.population),
        "continuous_time": contract.clock.mode == "continuous",
        "physics": contract.physics is not None,
        "entity_dynamics": contract.physics is not None and bool(contract.physics.per),
        "atomic_turns": any(stage.atomic or stage.valid for stage in contract.stage_list()),
        "time_limits": any(stage.time_limit is not None for stage in contract.stage_list()),
        "lifecycle_hooks": any(spec.on_create or spec.on_remove for spec in contract.types.values()),
        "delayed_or_lossy_messages": any("delay" in node or _lossy(node) for _, node in scan.effects
                                         if {"post", "emit", "wake"} & set(node)),
        "external_data": bool(contract.feeds),
    }
    return [name for name, present in found.items() if present]
