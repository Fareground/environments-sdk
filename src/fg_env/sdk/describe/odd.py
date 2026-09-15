"""An ODD-protocol description (Grimm et al., 2020) of a contract, written from the contract itself.

Only what the contract states is written; decisions made by participants at run time are named as such.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

from .. import contract as C
from ..registry import MECHANISMS

__all__ = ["odd_markdown"]


def _cell(value: Any) -> str:
    """A markdown table cell: pipes escaped, newlines flattened, empty as a dash."""
    if value is None or value == {} or value == []:
        return "—"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    return text or "—"


def _table(header: List[str], rows: List[List[Any]]) -> List[str]:
    if not rows:
        return []
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + \
        ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows] + [""]


def _code(values: List[Any]) -> List[str]:
    if not values:
        return []
    lines = [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str) for v in values]
    return ["```"] + lines + ["```", ""]


def _range(low: Any, high: Any) -> str:
    if low is None and high is None:
        return ""

    def side(value: Any) -> str:
        if value is None:
            return ""
        return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)

    return f"{side(low)}…{side(high)}"


def _bullets(items: List[str], empty: str) -> List[str]:
    """Items as a bullet list, or the ``empty`` sentence (nothing at all when that is blank)."""
    if items:
        return [f"- {item}" for item in items] + [""]
    return [empty, ""] if empty else []


def odd_markdown(contract: C.Contract, metadata: Mapping[str, Any]) -> str:
    lines = [f"# {contract.name} — ODD description", "",
             "_Written from the contract by `fg_env.describe`, following the ODD protocol (Overview, Design concepts, "
             "Details). Participants — LLM agents, coded policies or people — are attached when the environment runs, "
             "so their decision making is outside this description._", ""]
    for section in (_purpose, _entities, _process, _concepts, _initialisation, _inputs, _submodels):
        lines += section(contract, metadata)
    lines += _summary(metadata)
    return "\n".join(lines).rstrip() + "\n"


def _purpose(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    lines = ["## 1. Purpose and patterns", ""]
    for text in (contract.description, contract.brief.situation):
        if text:
            lines += [text.strip(), ""]
    outputs = [[name, spec.type, spec.description, f"`{spec.expr}`"] for name, spec in contract.outputs.items()]
    metrics = [[name, spec.unit, spec.description, f"`{spec.expr}`"] for name, spec in contract.metrics.items()]
    lines += ["Patterns the model is evaluated by (outputs):", ""] + _table(["output", "type", "meaning", "computed as"], outputs) \
        if outputs else ["No outputs are declared.", ""]
    if metrics:
        lines += ["Tracked every round (metrics):", ""] + _table(["metric", "unit", "meaning", "computed as"], metrics)
    return lines


def _props_table(props: Mapping[str, C.PropSpec]) -> List[str]:
    return _table(["variable", "type", "initial value", "range", "private", "unit", "meaning"],
                  [[name, spec.type or "(from default)", spec.default, _range(spec.min, spec.max),
                    "yes" if spec.private else "", spec.unit, spec.description] for name, spec in props.items()])


def _entities(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    counts = metadata.get("players_by_type", {})
    lines = ["## 2. Entities, state variables and scales", "", "### Entity types", ""]
    lines += _table(["type", "takes turns", "extends", "agents at start", "meaning"],
                    [[name, "yes" if contract.is_agent(name) else "no", spec.extends or "", counts.get(name, ""),
                      spec.description] for name, spec in contract.types.items()])
    for name in contract.types:
        props = contract.props_of(name)
        if props:
            lines += [f"### State variables of `{name}`", ""] + _props_table(props)
    if contract.world:
        lines += ["### Global state (`$world`)", ""] + _props_table(contract.world)
    if contract.relations:
        lines += ["### Relations", ""] + _table(["relation", "symmetric", "range", "link fields", "meaning"], [
            [name, "yes" if spec.symmetric else "no", _range(spec.min, spec.max), ", ".join(spec.props), spec.description]
            for name, spec in contract.relations.items()])
    if contract.records:
        lines += ["### Records (append-only logs)", ""] + _table(["record", "fields", "who can read an entry", "keeps"], [
            [name, ", ".join(f"{k}: {v}" for k, v in spec.fields.items()), spec.visible, spec.keep or "all"]
            for name, spec in contract.records.items()])
    clock = contract.clock
    scale = [f"Time: {clock.mode} clock; one step is {clock.step} {clock.unit}(s)"]
    scale.append(f"at most {clock.rounds} rounds" if clock.mode == "rounds" else f"horizon {clock.horizon}")
    if clock.start:
        scale.append(f"starting {clock.start}")
    lines += ["### Scales", "", "; ".join(str(s) for s in scale) + "."]
    if contract.space is not None:
        kind = next(k for k in ("grid", "graph", "plane") if getattr(contract.space, k) is not None)
        lines.append(f"Space: {kind} {json.dumps(getattr(contract.space, kind).model_dump(), default=str)}.")
    if contract.physics is not None:
        lines.append(f"Continuous variables integrated each round (RK4, dt {contract.physics.dt}): "
                     + ", ".join(f"`{name}`" for name in contract.physics.vars) + ".")
    return lines + [""]


def _process(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    lines = ["## 3. Process overview and scheduling", "",
             "Each round: scheduled effects, external data feeds, start-phase events, a physics step (world variables, "
             "then per-entity dynamics), every stage in order, end-phase events, metric sampling, then invariant and "
             "end checks. Triggers fire the moment their condition becomes true, and lifecycle hooks the moment an "
             "entity is created or removed.", ""]
    lines += _table(["#", "stage", "turns", "who acts", "order", "runs when", "repeats until", "atomic", "time limit (s)",
                     "actions offered"], [
        [i + 1, s.name, s.turns, s.who or "every agent", s.order, s.when or "every round", s.until or "",
         "yes" if s.atomic or s.valid else "", "" if s.time_limit is None else s.time_limit,
         s.actions if isinstance(s.actions, str) else json.dumps(s.actions)]
        for i, s in enumerate(contract.stage_list())])
    if contract.events:
        lines += ["Events:", ""] + _table(["event", "phase", "fires", "for each", "headline"], [
            [e.name or f"event {i + 1}", e.phase, _fires(e), e.each or "", e.say or ""] for i, e in enumerate(contract.events)])
    if contract.triggers:
        lines += ["Triggers:", ""] + _table(["trigger", "when", "once"], [
            [t.name or f"trigger {i + 1}", f"`{t.when}`", "yes" if t.once else "no"] for i, t in enumerate(contract.triggers)])
    ends = [f"`{e.name or f'end {i + 1}'}` when `{e.when}`" + (f", winner `{e.winner}`" if e.winner else "")
            for i, e in enumerate(contract.end)]
    lines += ["The run ends:", ""] + _bullets(ends + [f"after {contract.clock.rounds} rounds at the latest"
                                                      if contract.clock.mode == "rounds" else "at the time horizon"], "")
    return lines


def _fires(event: C.EventSpec) -> str:
    parts = []
    if event.at is not None:
        parts.append(f"at round {event.at}")
    if event.every:
        parts.append(f"every {event.every} rounds")
    if event.when:
        parts.append(f"when `{event.when}`")
    if event.chance is not None:
        parts.append(f"with chance {event.chance}")
    if event.arms:
        parts.append(f"in arms {', '.join(event.arms)}")
    return ", ".join(parts) or "every round"


def _concepts(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    evidence: Mapping[str, List[str]] = metadata.get("evidence", {})
    lines = ["## 4. Design concepts", ""]
    if contract.brief.rules:
        lines += ["**Basic principles.** " + contract.brief.rules.strip(), ""]
    roles = [f"`{name}`: {text.strip()}" for name, text in contract.brief.roles.items()]
    lines += ["**Objectives.** What each agent type is told it wants:", ""] + _bullets(roles, "No role objectives are stated.")
    policies = ", ".join(f"`{name}`" for name in contract.policies)
    lines += ["**Adaptation and learning.** Decisions come from the participants attached at run time. "
              + (f"The contract declares coded policies: {policies}." if policies else "The contract declares no coded policies."), ""]
    observations = metadata.get("observations", {})
    sensing = [f"`{kind}` agents read views: " + ", ".join(f"`{v}`" for v in views)
               for kind, views in observations.get("views", {}).items()]
    sensing += [f"record `{name}` is readable by {who}" for name, who in observations.get("records", {}).items()]
    sensing += [f"entities of type `{kind}` can be inspected by {who}" for kind, who in observations.get("inspect", {}).items()]
    if observations.get("spectator"):
        sensing.append("spectator views, rendered for reports and UIs and never shown to an agent: "
                       + ", ".join(f"`{name}`" for name in observations["spectator"]))
    lines += ["**Sensing.** Agents read a static brief and a per-turn update in plain text, and act through typed tools.", ""]
    lines += _bullets(sensing, "")
    lines += [f"**Information.** {metadata.get('information', 'unknown')}.", ""] + _bullets(evidence.get("information", []), "")
    interaction = [f"`{name}` ({', '.join(spec.by if isinstance(spec.by, list) else [spec.by])}) targets "
                   + ", ".join(f"`{p}` ({ps.of})" for p, ps in spec.params.items() if ps.type == "entity")
                   for name, spec in contract.actions.items() if any(ps.type == "entity" for ps in spec.params.values())]
    lines += ["**Interaction.** Actions that name other entities:", ""] + _bullets(interaction, "No action names another entity directly.")
    lines += [f"**Stochasticity.** {metadata.get('chance_mode', 'unknown')}.", ""] + _bullets(evidence.get("chance_mode", []), "")
    mixes = [f"population of `{p.type}` mixes " + ", ".join(m.name for m in p.mix) for p in contract.population if p.mix]
    networks = [f"relation `{name}`" for name in contract.relations]
    if mixes or networks:
        lines += ["**Collectives.**", ""] + _bullets(mixes + networks, "")
    lines += ["**Observation.** Measured through the metrics and outputs listed in section 1.", ""]
    return lines


def _initialisation(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    lines = ["## 5. Initialisation", "",
             "Every run has one seed; each random stream is derived from it, so a seed replays the run exactly.", ""]
    lines += _table(["entity", "type", "name", "starting values"], [
        [eid, spec.type, spec.name or "", spec.props] for eid, spec in contract.entities.items()])
    groups = []
    for p in contract.population:
        how = f"from `{p.from_}`" if p.from_ else ""
        how += f" count {p.count}" if p.count is not None else ""
        how += f", where `{p.where}`" if p.where else ""
        how += f", weighted by `{p.weight}`" if p.weight else ""
        groups.append([p.type, how.strip(", "), p.props, ", ".join(m.name for m in p.mix)])
    lines += _table(["generated type", "how many", "values", "archetypes"], groups)
    links = [[spec.relation, spec.graph or "explicit", spec.among or f"{spec.from_} → {spec.to}", spec.p or spec.degree or ""]
             for spec in contract.links]
    lines += _table(["relation", "network", "among", "parameter"], links)
    return lines


def _inputs(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    lines = ["## 6. Input data", ""]
    lines += _table(["input", "type", "default", "range", "required", "source", "unit", "meaning"], [
        [name, spec.type, spec.default, _range(spec.min, spec.max), "yes" if spec.required else "", spec.source or "",
         spec.unit, spec.description] for name, spec in contract.inputs.items()]) or ["No inputs are declared.", ""]
    if contract.arms:
        lines += ["Experiment arms:", ""] + _table(["arm", "inputs", "patches", "meaning"], [
            [name, spec.inputs, ", ".join(spec.patch), spec.description] for name, spec in contract.arms.items()])
    if contract.feeds:
        lines += ["External data (feeds answered by host adapters; every answer is recorded on the host tape):", ""] + _table(
            ["feed", "host", "written into", "every (rounds)", "when", "without a host"], [
                [name, feed.host, feed.into, feed.every, feed.when or "",
                 "the run stops" if feed.fallback is None else feed.fallback] for name, feed in contract.feeds.items()])
    return lines


def _submodels(contract: C.Contract, metadata: Mapping[str, Any]) -> List[str]:
    lines = ["## 7. Submodels", ""]
    for name, spec in contract.actions.items():
        by = ", ".join(spec.by if isinstance(spec.by, list) else [spec.by])
        lines += [f"### Action `{name}` (by {by})", ""]
        if spec.description:
            lines += [spec.description, ""]
        lines += _table(["parameter", "type", "choices", "meaning"], [
            [p, ps.type, ps.of or ps.values or _range(ps.min if not isinstance(ps.min, str) else None,
                                                     ps.max if not isinstance(ps.max, str) else None), ps.description]
            for p, ps in spec.params.items()])
        conditions = [f"`{c.expr}`" + (f" — {c.why}" if c.why else "") for c in spec.when]
        if conditions:
            lines += ["Allowed when:", ""] + _bullets(conditions, "")
        if spec.chance is not None:
            lines += [f"Succeeds with chance `{spec.chance}`; otherwise:", ""] + _code(spec.otherwise)
        lines += ["Effects:", ""] + _code(spec.do) if spec.do else []
    for index, event in enumerate(contract.events):
        if event.do:
            lines += [f"### Event `{event.name or f'event {index + 1}'}`", ""] + _code(event.do)
    for stage in contract.stage_list():
        if stage.valid or stage.on_timeout:
            lines += [f"### Turn rules of stage `{stage.name}`", ""]
            lines += ["A turn stands only when:", ""] + _bullets(
                [f"`{c.expr}`" + (f" — {c.why}" if c.why else "") for c in stage.valid], "") if stage.valid else []
            lines += ["When a turn runs out of time:", ""] + _code(stage.on_timeout) if stage.on_timeout else []
    for kind, type_spec in contract.types.items():
        for hook in ("on_create", "on_remove"):
            if getattr(type_spec, hook):
                lines += [f"### Lifecycle hook `{kind}.{hook}`", ""] + _code(getattr(type_spec, hook))
    lines += _dynamics(contract)
    for name, formula in contract.defs.items():
        lines += [f"### Formula `${name}({', '.join(formula.args)})`", ""] \
            + ([formula.description, ""] if formula.description else []) + _code([formula.expr])
    for name, block in contract.blocks.items():
        lines += [f"### Effect block `{name}({', '.join(block.args)})`", ""] + _code(block.do)
    for name, config in contract.mechanisms.items():
        kind = str(config.get("kind", ""))
        doc = MECHANISMS[kind].doc.strip().splitlines()[0] if kind in MECHANISMS else ""
        lines += [f"### Mechanism `{name}` ({kind})", "", doc, ""]
    invariants = [f"`{i.expr}`" + (f" — {i.why}" if i.why else "") for i in contract.invariants]
    if invariants:
        lines += ["### Invariants (checked after every change)", ""] + _bullets(invariants, "")
    return lines


def _dynamics(contract: C.Contract) -> List[str]:
    physics = contract.physics
    if physics is None:
        return []
    lines = [f"### Continuous dynamics (dt {physics.dt:g}, {physics.substeps} sub-steps a round)", ""]
    lines += _table(["variable", "starts at", "rate", "noise", "range"], [
        [name, var.start, var.rate or "", var.noise or "", _range(var.min, var.max)] for name, var in physics.vars.items()])
    for kind, dynamics in physics.per.items():
        lines += [f"### Per-entity dynamics of `{kind}`" + (f" (where `{dynamics.where}`)" if dynamics.where else ""), ""]
        lines += _table(["prop", "rate", "noise"], [[name, var.rate, var.noise or ""] for name, var in dynamics.vars.items()])
        if dynamics.write:
            lines += ["Then sets:", ""] + _bullets([f"`{prop}` = `{expr}`" for prop, expr in dynamics.write.items()], "")
    return lines


def _summary(metadata: Mapping[str, Any]) -> List[str]:
    length: Dict[str, Any] = metadata.get("max_game_length", {})
    space: Dict[str, Any] = metadata.get("action_space", {})
    rows = [["dynamics", metadata.get("dynamics")], ["chance", metadata.get("chance_mode")],
            ["information", metadata.get("information")], ["utility", metadata.get("utility")],
            ["players", metadata.get("num_players")], ["fewest players", metadata.get("min_players")],
            ["most players", metadata.get("max_players")], ["longest game (rounds)", length.get("rounds")],
            ["most decisions", length.get("decisions")],
            ["action space", f"{space.get('kind')}" + (f", at most {space['size']} distinct actions" if space.get("size") else "")],
            ["external data", ", ".join(feed["feed"] for feed in metadata.get("external_data", [])) or "none"],
            ["concepts", ", ".join(metadata.get("concepts", [])) or "—"]]
    return ["## Game-theoretic summary", "", "Values that cannot be derived from the contract are `unknown`.", ""] + \
        _table(["property", "value"], [[k, "unknown" if v is None else v] for k, v in rows])
