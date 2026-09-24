"""Game metadata derived from a contract: what an algorithm or evaluator may assume, each with its evidence.

What the contract does not settle is ``None`` (for numbers) or ``"unknown"`` (for classes), and the evidence
says why. Nothing is guessed from names or from sample runs.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..actions.reads import inspect_rule
from ..contract import Contract, ParamSpec, StageSpec
from ..runtime.returns import utility_class
from . import walk

__all__ = ["game_metadata", "MAX_EVIDENCE"]

#: Most evidence lines kept per property (the rest are counted).
MAX_EVIDENCE = 12
_INPUT_REF = re.compile(r"\s*\$inputs\.([A-Za-z_][A-Za-z0-9_]*)\s*")
_MEASURE_REF = re.compile(r"\$(?:outputs|series)\.([A-Za-z_][A-Za-z0-9_]*)")
_RANDOM_GRAPHS = frozenset({"random", "small_world", "scale_free", "blocks"})
_MARKETS = frozenset({"market"})
_TALK = frozenset({"social", "decision.deliberation"})
_BOARDS = frozenset({"game.board"})
_CARDS = frozenset({"game.cards"})
_ROLES = frozenset({"groups.roles"})

Choices = int | str | None


def _capped(lines: Iterable[str]) -> list[str]:
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
        self.texts: list[tuple[str, str, frozenset[str]]] = []
        spectators = tuple(f"views.{name}." for name, view in contract.views.items() if _spectator(view))
        engine = contract.run_by_engine()
        for path, text in walk.texts(data):
            where = set(walk.roles(path, engine))
            if any(_record_field(path, prefix) for prefix in posts):
                where.add("shown")  # a post's fields become an entry agents read
            if path.startswith(spectators):
                where.clear()  # rendered for reports and UIs, never for an agent
            self.texts.append((path, text, frozenset(where)))
        shown = [text for _, text, where in self.texts if "shown" in where]
        rules = [text for _, text, where in self.texts if "rules" in where]
        sampled = contract.series_outputs()
        measured = {name for text in shown for name in _MEASURE_REF.findall(text) if name in sampled}
        exprs = contract.expr_defs()
        called = {name for text in shown for name in walk.calls(text) if name in exprs}
        shown += [sampled[name].sampled or "" for name in measured] + [exprs[name].expr or "" for name in called]
        self.shown_world: set[str] = set().union(*(walk.world_reads(text) for text in shown))
        self.rule_world: set[str] = set().union(*(walk.world_reads(text) for text in rules))
        self.shows_physics = any("$physics." in text for text in shown)
        self.created = [(path, node["create"]) for path, node in self.effects if "create" in node]


def _spectator(view: Any) -> bool:
    return "spectator" in (view.for_ if isinstance(view.for_, list) else [view.for_])


def _lossy(node: Mapping[str, Any]) -> bool:
    return node.get("drop") not in (None, 0, False) and bool({"post", "emit"} & set(node))


def _record_field(path: str, prefix: str) -> bool:
    if not path.startswith(prefix + "."):
        return False
    return re.split(r"[.\[]", path[len(prefix) + 1:])[0] not in ("post", "to", "author")


def game_metadata(contract: Contract, probe: Any = None, probe_error: str | None = None) -> dict[str, Any]:
    """Metadata for ``contract``; ``probe`` is an :class:`Env` built from it (for counts), or ``None`` with
    ``probe_error``."""
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
    utility, utility_evidence = utility_class(contract)
    evidence = {"dynamics": dynamics_evidence, "chance_mode": chance_evidence, "information": information_evidence,
                "utility": utility_evidence,
                "players": players_evidence, "max_game_length": length_evidence, "action_space": space_evidence}
    return {"name": contract.name, "dynamics": dynamics, "chance_mode": chance, "chance_during": during,
            "information": information, "utility": utility, "num_players": count, "min_players": low,
            "max_players": high, "players_by_type": by_type, "max_game_length": length, "action_space": space,
            "observations": _observations(contract), "concepts": _concepts(contract, scan),
            "external_data": [{"feed": name, "host": feed.host, "into": feed.into, "every": feed.every}
                              for name, feed in contract.feeds.items()],
            "evidence": {key: _capped(lines) for key, lines in evidence.items()}}


def _acting(contract: Contract) -> list[Any]:
    return [stage for stage in contract.stage_list() if stage.actions != []]


def _dynamics(contract: Contract) -> tuple[str, list[str]]:
    if not contract.agent_types():
        return "none", ["no type takes turns"]
    acting = _acting(contract)
    if not acting:
        return "none", ["no stage offers actions"]
    kinds = list(dict.fromkeys(stage.turns for stage in acting))
    return (kinds[0] if len(kinds) == 1 else "mixed"), [_stage_note(s) for s in acting]


def _stage_note(stage: StageSpec) -> str:
    notes = [f"stage {stage.name}: {stage.turns} turns"]
    if stage.valid:
        notes.append("atomic (a turn's actions stand or fall together)")
    return ", ".join(notes)


def _chance(contract: Contract, scan: _Scan) -> tuple[str, list[str], list[str]]:
    functions, ops = walk.random_functions(), walk.random_ops()
    setup: list[str] = []
    play: list[str] = []
    for path, text, where in scan.texts:
        names = sorted(walk.calls(text) & functions)
        if names and where:
            (setup if where == {"setup"} else play).append(f"{path} calls " + ", ".join(f"${n}" for n in names))
    nodes: list[str] = []
    for path, node in scan.effects:
        if "chance" in node:
            nodes.append(f"{path} is a chance node with listed outcomes (fg_env.rl.game enumerates them)")
        for op in sorted(walk.ops_in(node) & ops - {"chance"}):
            play.append(f"{path} uses the {op} op, which draws at random")
        if _lossy(node):
            play.append(f"{path} may lose the message (drop)")
    play += [f"stage {s.name} wakes agents in random order" for s in contract.stage_list() if s.order == "random"]
    if contract.physics is not None:
        play += [f"mechanisms.physics.vars.{name}.noise is a random term" for name, var in contract.physics.vars.items()
                 if var.noise]
        play += [f"mechanisms.physics.per.{kind}.vars.{name}.noise is a random term"
                 for kind, dynamics in contract.physics.per.items()
                 for name, var in dynamics.vars.items() if var.noise]
    for _, path, link in contract.starting_links():
        if link.graph in _RANDOM_GRAPHS or (link.graph is not None and link.p is not None):
            setup.append(f"{path} draws a {link.graph} network")
    for key, group in contract.entities.items():
        if group.weight or (group.from_ is not None and group.count is not None):
            setup.append(f"entities.{key} samples rows")
    during = [label for label, found in (("setup", setup), ("play", play)) if found]
    if during:
        return "sampled", during, setup + play + nodes
    return ("explicit" if nodes else "deterministic"), (["play"] if nodes else []), nodes


def _information(contract: Contract, scan: _Scan) -> tuple[str, list[str]]:
    hiding: list[str] = []
    private = [f"types.{name}.props.{prop}" for name, spec in contract.types.items()
               for prop, p in spec.props.items() if p.private]
    private += [f"world.{prop}" for prop, p in contract.world.items() if p.private]
    if private:
        hiding.append("private props: " + ", ".join(private))
    for name, spec in contract.types.items():
        if isinstance(spec.inspect, str):
            hiding.append(f"types.{name}.inspect limits who may inspect it: `{spec.inspect}`")
    unannounced = [f"actions.{name}" for name, a in contract.actions.items() if a.silent]
    if unannounced:
        hiding.append("unannounced actions (others are not told they happened): " + ", ".join(unannounced))
    hiding += [f"records.{name} is readable only when `{r.visible}`" for name, r in contract.records.items()
               if r.visible.strip() != "all"]
    hiding += [f"{path} reaches only `{node['to']}`" for path, node in scan.effects
               if ("post" in node or "emit" in node) and node.get("to") not in (None, "", [])]
    hiding += [f"{path} can lose messages on the way (drop)" for path, node in scan.effects if _lossy(node)]
    hiding += [f"entities.{eid}.brief is private to " + ("each entity it generates" if e.generates else "that entity")
               for eid, e in contract.entities.items() if e.brief]
    hiding += [f"stage {s.name}: agents choose without seeing each other's choices" for s in _acting(contract)
               if s.turns == "simultaneous"]
    if hiding:
        return "imperfect", hiding
    unknown = [f"entities of type {name} cannot be inspected, and the scan does not check that views show their state"
               for name in contract.types if inspect_rule(contract, name) is False]
    unseen = sorted(scan.rule_world - scan.shown_world)
    if unseen:
        unknown.append("world props the rules read but no view, brief or message shows: " + ", ".join(unseen))
    if contract.physics is not None and not scan.shows_physics:
        unknown.append("physics variables drive the world but no view or brief reads $physics")
    if unknown:
        return "unknown", unknown
    return "perfect", ["no private props, hidden records, targeted messages, private briefs, viewer-dependent "
                       "inspection or simultaneous choices, and every world prop the rules read is shown"]


def _players(contract: Contract, scan: _Scan, probe: Any) -> tuple[int | None, int | None, int | None,
                                                                    dict[str, int], list[str]]:
    evidence: list[str] = []
    by_type: dict[str, int] = {}
    count: int | None = None
    if probe is not None:
        for entity in probe.world.entities.values():
            if probe.contract.is_agent(entity.entity_type):
                by_type[entity.entity_type] = by_type.get(entity.entity_type, 0) + 1
        count = sum(by_type.values())
    fixed = sum(1 for e in contract.named_entities().values() if contract.is_agent(e.type))
    low: int | None = fixed
    high: int | None = fixed
    for key, group in contract.entities.items():
        path = f"entities.{key}"
        if not group.generates or not contract.is_agent(group.type):
            continue
        ref = _INPUT_REF.fullmatch(group.count) if isinstance(group.count, str) else None
        spec = contract.inputs.get(ref.group(1)) if ref else None
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


def _add(total: int | None, amount: int) -> int | None:
    return None if total is None else total + amount


def _length(contract: Contract, scan: _Scan, probe: Any, players: int | None) -> tuple[dict[str, Any], list[str]]:
    clock = contract.clock
    evidence: list[str] = []
    rounds: int | None = None
    if isinstance(clock.rounds, int):
        rounds = clock.rounds
    elif probe is not None:
        rounds = probe.world.rounds
        evidence.append(f"clock.rounds is `{clock.rounds}`: {rounds} with these inputs")
    else:
        evidence.append(f"clock.rounds is `{clock.rounds}` and the contract could not be built")
    wakes = [path for path, node in scan.effects if "wake" in node]
    acting = _acting(contract)
    decisions: int | None = None
    if rounds is None:
        pass
    elif wakes:
        evidence.append(f"{wakes[0]} wakes agents for extra turns")
    elif any(stage.valid for stage in acting):
        evidence.append(f"stage {next(s.name for s in acting if s.valid)} replays a turn that breaks `valid`")
    elif any(isinstance(stage.passes, str) or isinstance(stage.max_actions, str) for stage in acting):
        counted = next(s.name for s in acting if isinstance(s.passes, str) or isinstance(s.max_actions, str))
        evidence.append(f"stage {counted} takes as many passes and actions per turn as its inputs give")
    elif players is None:
        evidence.append("the number of players is not fixed")
    else:
        per_round = sum((s.passes or (10 if s.until else 1)) * s.max_actions for s in acting) * players
        decisions = rounds * per_round
        evidence.append(f"at most {rounds} rounds × {per_round} decisions a round "
                        "(passes × actions per turn, over the stages, × players)")
    return {"rounds": rounds, "decisions": decisions}, evidence


def _action_space(contract: Contract, scan: _Scan, probe: Any) -> tuple[dict[str, Any], list[str]]:
    created: set[str] = set()
    dynamic = False
    for _, kind in scan.created:
        if isinstance(kind, str) and kind in contract.types:
            created.add(kind)
        else:
            dynamic = True
    counts: dict[str, int] = {}
    if probe is not None:
        for entity in probe.world.entities.values():
            for kind in contract.lineage(entity.entity_type):
                counts[kind] = counts.get(kind, 0) + 1
    evidence: list[str] = []
    per_action: dict[str, int | str] = {}
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


def _choices(contract: Contract, spec: ParamSpec, counts: Mapping[str, int], created: set[str], dynamic: bool,
             probed: bool) -> tuple[Choices, str]:
    if spec.type == "bool":
        return 2, ""
    if spec.type == "enum":
        return (len(spec.values), "") if isinstance(spec.values, list) else (None, "an enum whose values come from "
                                                                                   f"`{spec.values}`")
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


def _observations(contract: Contract) -> dict[str, Any]:
    agents = contract.agent_types()
    views: dict[str, list[str]] = {kind: [] for kind in agents}
    for name, view in contract.views.items():
        audience = view.for_ if isinstance(view.for_, list) else [view.for_]
        for kind in agents:
            if "all" in audience or any(contract.is_a(kind, a) for a in audience):
                views[kind].append(name + (" (on request)" if view.look else ""))
    records = {name: "everyone" if r.visible.strip() == "all" else f"only when `{r.visible}`"
               for name, r in contract.records.items()}
    inspect: dict[str, str] = {}
    for kind in contract.types:
        rule = inspect_rule(contract, kind)
        inspect[kind] = "everyone" if rule is True else "only itself" if rule is False else f"only when `{rule}`"
    return {"text": True, "struct": False, "tensor": False, "views": views, "records": records, "inspect": inspect,
            "spectator": [name for name, view in contract.views.items() if _spectator(view)]}


def _concepts(contract: Contract, scan: _Scan) -> list[str]:
    kinds = walk.mechanism_kinds(contract)
    space = contract.space
    found = {
        "board": (space is not None and space.grid is not None) or bool(kinds & _BOARDS),
        "graph_space": space is not None and space.graph is not None,
        "plane": space is not None and space.plane is not None,
        "cards": bool(kinds & _CARDS),
        "hidden_roles": bool(kinds & _ROLES),
        "communication": bool(kinds & _TALK)
        or any("post" in node and path.startswith("actions.") for path, node in scan.effects),
        "markets": bool(kinds & _MARKETS),
        "networks": bool(contract.relations),
        "population": any(spec.generates for spec in contract.entities.values()),
        "physics": contract.physics is not None,
        "entity_dynamics": contract.physics is not None and bool(contract.physics.per),
        "atomic_turns": any(stage.valid for stage in contract.stage_list()),
        "lifecycle_hooks": any(event.on.startswith(("create.", "remove.")) for event in contract.events),
        "delayed_or_lossy_messages": any("delay" in node or _lossy(node) for _, node in scan.effects
                                         if {"post", "emit"} & set(node)),
        "external_data": bool(contract.feeds),
    }
    return [name for name, present in found.items() if present]
