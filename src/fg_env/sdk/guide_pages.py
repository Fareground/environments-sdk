"""The guide's generated pages: one per contract section, function group, mechanism family and mode.

Everything here is read from the code (models, registered functions, effect ops, mechanism families), so a
page cannot drift from what the engine accepts.
"""
from __future__ import annotations

import json
import typing
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from . import contract as C
from .effects import EFFECT_OPS
from .expr import FUNCTIONS, FunctionSpec
from .guide_text import EFFECT_EXAMPLES, EFFECTS, EXPRESSIONS
from .registry import FAMILIES, OPS, FamilySpec, ModeSpec

__all__ = ["SECTIONS", "section_page", "roots_table", "expressions_page", "FUNCTION_GROUPS", "function_groups",
           "functions_index", "functions_page", "effects_page", "mechanisms_page", "family_page", "mode_page"]

#: Roots every expression may read, wherever it is written.
EVERYWHERE = "$inputs $world $physics $clock $round $stage $metrics $series $arm"

#: (section, where in it, the extra roots available there).
ROOTS: List[Tuple[str, str, str]] = [
    ("actions", "when", "$actor ($params too: such a requirement is checked when the action is called)"),
    ("actions", "params.*.where", "$actor $it $i $params (earlier params)"),
    ("actions", "params.*.min/max/values/default", "$actor $params (earlier params)"),
    ("actions", "chance/do/otherwise/outcome/announce/terminal", "$actor $params + locals"),
    ("stages", "who/order/first_wake", "$it $i"),
    ("stages", "brief/time_limit/interval/on_wake/on_idle/on_turn_end/on_timeout", "$actor"),
    ("stages", "valid (expr and why)", "$actor $pending"),
    ("stages", "when/until/on_enter/on_exit", "—"),
    ("views", "when/of", "$actor"),
    ("views", "where/sort/show", "$actor $it $i"),
    ("views", "with for: spectator", "no $actor ($it $i in lists)"),
    ("records", "visible", "$viewer $it (entry)"),
    ("records", "show", "$it (entry: its fields directly, $it.text, plus author, round, seq, stage, to)"),
    ("events", "where/do (with each)", "$it $i (or the `as` name)"),
    ("triggers", "when/do/say", "—"),
    ("population", "where/weight", "$row"),
    ("population", "props/id/name", "$row $i ($i counts from 1)"),
    ("population", "brief", "$actor $row $i"),
    ("entities", "brief", "$actor"),
    ("types", "inspect", "$viewer $it"),
    ("types", "on_create/on_remove", "$it (the entity) + locals"),
    ("relations", "props.*.default", "$from $to"),
    ("links", "props", "$from $to (+ $row with `rows`)"),
    ("physics", "per.*.read/where", "$it"),
    ("feeds", "query/when/fallback", "—"),
    ("defs", "expr", "the def's args"),
    ("blocks", "do", "the block's args + locals"),
    ("policies", "rules.*", "$actor ($it $i with `each`)"),
    ("metrics", "*", "—"),
    ("outputs", "*", "$outputs (earlier outputs) $result (winner, ended_by)"),
    ("end", "when/winner/say", "—"),
    ("game", "seat", "$it $i"),
    ("game", "returns/rewards", "$actor $result (winner, ended_by)"),
    ("invariants", "*", "—"),
]

#: (section, models documented, shape, what it declares). The order is the guide's.
SECTIONS: List[Tuple[str, List[Type[BaseModel]], str, str]] = [
    ("brief", [C.Brief], "Brief", "Static text every agent reads first: the situation, the rules, and per-type role text."),
    ("clock", [C.Clock], "Clock", "How long a run lasts (`rounds`, default 20) and what one round is called."),
    ("inputs", [C.InputSpec], "{name: InputSpec}", "Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables."),
    ("world", [C.PropSpec], "{prop: default | PropSpec}", "Global properties ($world.x)."),
    ("assets", [C.AssetSpec], "{asset: AssetSpec}", "Files beside the contract — images, PDFs, text, audio — delivered to agents under the visibility rules; see guide('assets')."),
    ("types", [C.TypeSpec, C.PropSpec], "{type: TypeSpec}", "Kinds of entities and their properties; `agent: true` makes a type act."),
    ("entities", [C.EntitySpec], "{id: EntitySpec}", "Named entities (the name defaults to the id)."),
    ("population", [C.PopulationSpec], "[PopulationSpec]", "Generated entities: a count, or one per data row, with sampled traits."),
    ("records", [C.RecordSpec], "{record: RecordSpec}", "Append-only logs (chat, reviews, bids) with per-viewer visibility; written with `post`."),
    ("actions", [C.ActionSpec, C.ParamSpec, C.Condition], "{action: ActionSpec}", "What agents can do: each is one typed tool with requirements and atomic effects."),
    ("stages", [C.StageSpec], "[StageSpec]", "The steps of every round: who acts, how (sequential or sealed simultaneous), which actions."),
    ("views", [C.ViewSpec], "{view: ViewSpec}", "What agents read each turn: single lines or ranked, filtered lists."),
    ("events", [C.EventSpec], "[EventSpec]", "What the world does at a set point of a round: at the start or end, on given rounds, every N rounds, when a condition holds, or by chance."),
    ("triggers", [C.TriggerSpec], "[TriggerSpec]", "What the world does the moment a condition becomes true (checked after every action and effect block), unlike an event, which runs at a set point of the round."),
    ("end", [C.EndSpec], "[EndSpec]", "Conditions that end the run early, with an optional winner ($result.winner in outputs)."),
    ("metrics", [C.MetricSpec], "{metric: expr | MetricSpec}", "Values sampled every round ($metrics.x latest, $series.x every round)."),
    ("outputs", [C.OutputSpec], "{output: expr | OutputSpec}", "The typed results of a run."),
    ("invariants", [C.InvariantSpec], "[expr | InvariantSpec]", "Rules that must always hold; a violation fails the run."),
    ("mechanisms", [], "{name: {kind, mode, ...config}}", "Native building blocks by family (markets, voting, cards, roles …): see guide('mechanisms')."),
    ("game", [C.GameSpec], "GameSpec", "Seats and what each scores, for tournaments, game search and gyms."),
    ("space", [C.Space, C.GridSpace, C.GraphSpace, C.PlaneSpace, C.LayerSpec], "Space", "Positions: a grid, a graph of places or a plane, with values on cells."),
    ("relations", [C.RelationSpec], "{relation: RelationSpec}", "Typed links between entities (trust, follows), with fields."),
    ("links", [C.LinkSpec], "[LinkSpec]", "Links made at build: listed, from data rows, or generated networks."),
    ("physics", [C.PhysicsSpec, C.PhysicsVar, C.EntityDynamics, C.EntityVar], "PhysicsSpec", "Continuous variables integrated every round (world-level and per entity)."),
    ("feeds", [C.FeedSpec], "{feed: FeedSpec}", "External data written into world props or records, answered by host adapters."),
    ("policies", [C.PolicySpec, C.PolicyRule], "{policy: PolicySpec}", "Coded participants as rules, for crowds and baselines (`policy:<name>`)."),
    ("arms", [C.ArmSpec], "{arm: ArmSpec}", "Experiment variants: input overrides or contract patches."),
    ("defs", [C.DefSpec], "{name: expr | DefSpec}", "Reusable expressions, called like built-ins: $utility($actor, 3)."),
    ("blocks", [C.BlockSpec], "{name: BlockSpec}", "Reusable effect lists, run with {\"block\": name, \"with\": {...}}."),
    ("imports", [], "[path]", "Contract files merged into this one (relative to it, inside its folder); this contract's own entries win, and imported files may import others."),
]

_SECTION_INDEX = {name: (models, shape, doc) for name, models, shape, doc in SECTIONS}


def _roots_rows(section: Optional[str] = None) -> List[str]:
    return [f"| {name}.{where} | {roots} |" if section is None else f"| {where} | {roots} |"
            for name, where, roots in ROOTS if section in (None, name)]


def roots_table() -> str:
    return "\n".join(["| where | extra roots |", "|---|---|", *_roots_rows()])


def expressions_page() -> str:
    return EXPRESSIONS.replace("ROOTS_TABLE", roots_table())


def section_page(section: str) -> str:
    models, shape, doc = _SECTION_INDEX[section]
    lines = [f"## `{section}`: {shape}", "", doc]
    rows = _roots_rows(section)
    if rows:
        lines += ["", f"Roots (plus everywhere: {EVERYWHERE}):", "| where | extra roots |", "|---|---|", *rows]
    lines += ["", *[_fields(model) for model in models]] if models else []
    return "\n".join(lines)


# -- functions ------------------------------------------------------------------

#: Group of each core function (the rest are grouped by the module that registers them).
_CORE_GROUPS = {
    "collections": "count sum avg min max median quantile stdev top bottom filter map pick any all ids len first last "
                   "unique tally mode sort reverse slice range flatten dict keys values get is",
    "world": "entity exists records events seen asset",
    "space": "relation linked link links neighbors distance",
    "math": "abs floor ceil sqrt exp log round clamp pct",
    "random": "random chance uniform randint normal lognormal beta exponential poisson choice sample shuffle",
    "text": "text lower contains join fmt",
}
_MODULE_GROUPS = {
    "stdlib.mathx": "math", "stdlib.linalg": "math", "stdlib.dists": "random", "stdlib.strings": "text",
    "stdlib.words": "text", "stdlib.lists": "lists", "stdlib.sets": "lists", "stdlib.stats": "stats",
    "stdlib.scoring": "stats", "space_functions": "space", "networks": "space", "stdlib.puzzles": "game",
    "mechanisms._common": "conditions", "mechanisms.card_scoring": "game", "mechanisms.cards": "game",
    "mechanisms.econ_assets": "economy", "mechanisms.market_stats": "market", "mechanisms.book_functions": "market",
    "mechanisms.book_rules": "market",
    "mechanisms.package_auction": "market", "mechanisms.auction_reads": "market", "mechanisms.memory": "mind",
}
#: What each non-family group holds, in the order the guide lists them.
FUNCTION_GROUPS = {
    "collections": "counting, summing, ranking and filtering lists and entity types",
    "world": "entities, records, events and what agents were shown",
    "math": "arithmetic, trigonometry, interpolation, linear algebra",
    "random": "seeded draws and distributions",
    "text": "text, formatting and word games",
    "lists": "list and map manipulation, sets",
    "stats": "statistics, time series and forecast scores",
    "space": "grids, graphs, networks and links",
}
_OTHER = "other"


def _group(spec: FunctionSpec) -> str:
    for group, names in _CORE_GROUPS.items():
        if spec.name in names.split():
            return group
    module = spec.impl.__module__.removeprefix("fg_env.sdk.")
    if module in _MODULE_GROUPS:
        return _MODULE_GROUPS[module]
    families = sorted({name for name, family in FAMILIES.items()
                       for mode in family.modes.values() if mode.expand.__module__ == spec.impl.__module__})
    return families[0] if len(families) == 1 else _OTHER


def function_groups() -> Dict[str, List[FunctionSpec]]:
    """Every registered function by group: the general groups first, then mechanism families."""
    out: Dict[str, List[FunctionSpec]] = {group: [] for group in [*FUNCTION_GROUPS, *FAMILIES, _OTHER]}
    for spec in sorted(FUNCTIONS.values(), key=lambda s: s.name):
        out[_group(spec)].append(spec)
    return {group: specs for group, specs in out.items() if specs}


def functions_index() -> str:
    lines = ["## Functions", "", "Every function by group. Read one group's signatures and docs with "
             "`guide('functions.<group>')` (a mechanism family's functions are also on its page).", ""]
    for group, specs in function_groups().items():
        about = FUNCTION_GROUPS.get(group) or (FAMILIES[group].doc if group in FAMILIES else "")
        lines.append(f"- `{group}` ({about.rstrip('.')}): " + " ".join(f"${s.name}" for s in specs))
    return "\n".join(lines)


def functions_page(group: str) -> str:
    specs = function_groups()[group]
    return "\n".join([f"## Functions: {group}", "", *[f"- `${spec.signature}` — {spec.doc}" for spec in specs]])


# -- effects --------------------------------------------------------------------

def effects_page() -> str:
    lines = [f"- `{op}`: {EFFECT_EXAMPLES[op]}" for op in EFFECT_OPS]
    lines += [f"- `{name}`: {spec.example}" for name, spec in OPS.items() if spec.select is None]
    for name, family in FAMILIES.items():
        if family.actions and any(family.actions.values()):
            lines.append(f"- `{name}`: {{\"{name}\": \"<{name} mechanism>\", \"action\": ...}} — actions: "
                         + "; ".join(f"{mode} {' '.join(_public(family, mode)) or '—'}" for mode in family.modes)
                         + f" (guide(\"{name}\"))")
    return EFFECTS.replace("OPS", "\n".join(lines))


# -- mechanisms -----------------------------------------------------------------

def mechanisms_page() -> str:
    from .mechanisms.families import SHARED

    lines = ["## Mechanisms (native building blocks)", "",
             "Declare `\"mechanisms\": {name: {\"kind\": <family>, \"mode\": <mode>, ...config}}`. Each expands into",
             "ordinary actions, stages, world props and events you can read, preview and override (declare the same",
             "name yourself to replace a generated part); `fg-env check` lists what each one generated and",
             "`fg-env expand file.json --mechanisms` shows all of it. A family has one effect op:",
             "`{\"<family>\": \"<mechanism name>\", \"action\": \"<action>\", ...}`. Read a family with",
             "`guide(\"<family>\")` and one mode with `guide(\"<family>.<mode>\")`.", "",
             "| kind | modes | for |", "|---|---|---|"]
    lines += [f"| `{name}` | {', '.join(family.modes) or '—'} | {family.doc} |" for name, family in FAMILIES.items()]
    lines += ["", "Every family names these the same way:"]
    lines += [f"- `{key}`: {meaning}" for key, meaning in SHARED.items()]
    return "\n".join(lines)


def family_page(name: str) -> str:
    """A family: what it is for, its shared names, its modes in one line each, and its functions."""
    family = FAMILIES[name]
    lines = [f"## Mechanism family `{name}`", "", family.doc, ""]
    if family.shared:
        lines += ["Named the same in every mode:"] + [f"- `{key}`: {meaning}" for key, meaning in family.shared.items()] + [""]
    lines += [f"Modes (`\"kind\": \"{name}\", \"mode\": ...`; read one with `guide('{name}.<mode>')`):"]
    lines += [f"- `{mode}`: {spec.doc.split('. ')[0].rstrip('.')}." for mode, spec in family.modes.items()]
    specs = function_groups().get(name, [])
    if specs:
        lines += ["", "Functions:", *[f"- `${spec.signature}` — {spec.doc}" for spec in specs]]
    return "\n".join(lines)


def mode_page(spec: ModeSpec) -> str:
    lines = [f"### `{spec.key}`", spec.doc, "", "Config:"]
    for field_name, info in spec.config.model_fields.items():
        default = "required" if info.is_required() else \
            f"default {json.dumps(info.get_default(call_default_factory=True), default=str)}"
        lines.append(f"- `{field_name}` ({default}): {info.description or ''}")
    nested = _nested_models(spec.config)
    if nested:
        lines += ["", "Nested config:"] + [_fields(model) for model in nested]
    actions = FAMILIES[spec.family].actions.get(spec.mode, {})
    public = [(action, op) for action, op in actions.items() if not op.internal]
    if public:
        lines += ["", f"Actions of the `{spec.family}` op:"]
        for action, op in public:
            keys = [k for k in op.keys if k not in (spec.family, "action")]
            needs = [k for k in op.required if k in keys]
            takes = f" — takes {', '.join(f'`{k}`' for k in keys)}" if keys else ""
            required = f" (needs {', '.join(f'`{k}`' for k in needs)})" if needs else ""
            lines.append(f"- `{action}`{takes}{required}: {op.example}")
    lines += ["", "```json", json.dumps({"mechanisms": {f"my_{spec.mode}": spec.example}}, ensure_ascii=False), "```"]
    return "\n".join(lines)


def _public(family: FamilySpec, mode: str) -> List[str]:
    """The actions of a mode an author writes (the mechanism's own bookkeeping actions left out)."""
    return [action for action, op in family.actions.get(mode, {}).items() if not op.internal]


# -- model fields ---------------------------------------------------------------

def _type_name(annotation: Any, field: str) -> str:
    if field in ("do", "otherwise", "on_enter", "on_exit", "on_create", "on_remove"):
        return "effects"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union:
        names = [_type_name(a, field) for a in args if a is not type(None)]
        return " | ".join(dict.fromkeys(names))
    if origin in (list, List):
        return f"[{_type_name(args[0], field)}]" if args else "list"
    if origin in (dict, Dict):
        return "object"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__
    return {str: "text", int: "int", float: "number", bool: "bool"}.get(annotation, "any")


def _fields(model: Type[BaseModel]) -> str:
    lines = [f"**{model.__name__}** — {(model.__doc__ or '').strip()}"]
    for name, info in model.model_fields.items():
        key = info.alias or name
        required = info.is_required()
        plain = info.default is PydanticUndefined or info.default in (None, "", [], {})
        default = "" if required or plain else f" = {json.dumps(info.default, default=str)}"
        flag = " (required)" if required else ""
        description = f" — {info.description}" if info.description else ""
        lines.append(f"- `{key}`: {_type_name(info.annotation, name)}{default}{flag}{description}")
    return "\n".join(lines)


def _nested_models(model: Type[BaseModel]) -> List[Type[BaseModel]]:
    """Models used inside ``model``'s fields (in lists, maps and optionals too), each once, depth first."""
    found: List[Type[BaseModel]] = []

    def visit(annotation: Any) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation is not model and annotation not in found:
                found.append(annotation)
                for info in annotation.model_fields.values():
                    visit(info.annotation)
            return
        for arg in typing.get_args(annotation):
            visit(arg)

    for info in model.model_fields.values():
        visit(info.annotation)
    return found
