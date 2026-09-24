"""The guide's generated pages: one per contract section, function group, mechanism family and mode.

Everything here is read from the code (models, registered functions, effect ops, mechanism families), so a
page cannot drift from what the engine accepts.
"""
from __future__ import annotations

import inspect
import json
import types
import typing
from typing import Any

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from .. import contract as C
from ..effects.runner import EFFECT_OPS
from ..expr import FUNCTIONS, FunctionSpec
from ..expr.calls import CORE_FUNCTIONS
from ..registry import FAMILIES, OPS, FamilySpec, ModeSpec
from .text import EFFECT_EXAMPLES, EFFECTS, EXPRESSIONS

__all__ = ["SECTIONS", "CORE_SECTIONS", "CORE_FUNCTIONS", "section_page", "roots_table", "expressions_page",
           "FUNCTION_GROUPS", "function_groups", "functions_index", "functions_page", "effects_page", "mechanisms_page",
           "family_page", "mode_page"]

#: Roots every expression may read, wherever it is written.
EVERYWHERE = "$inputs $world $physics $clock $round $stage $metrics $series $arm $pattern"

#: (section, where in it, the extra roots available there).
ROOTS: list[tuple[str, str, str]] = [
    ("actions", "when", "$actor ($params too: such a requirement is checked when the action is called)"),
    ("actions", "params.*.where", "$actor $it $i $params (earlier params)"),
    ("actions", "params.*.min/max/values/default", "$actor $params (earlier params)"),
    ("actions", "do/outcome/announce/terminal", "$actor $params + locals"),
    ("stages", "who/order", "$it $i"),
    ("stages", "brief", "$actor"),
    ("stages", "valid (expr and why)", "$actor $pending"),
    ("stages", "when/until", "—"),
    ("views", "when/of", "$actor"),
    ("views", "where/sort/show", "$actor $it $i"),
    ("views", "with for: spectator", "no $actor ($it $i in lists)"),
    ("records", "visible", "$viewer $it (entry)"),
    ("records", "show", "$it (entry: its fields directly, $it.text, plus author, round, seq, stage, to)"),
    ("events", "on: round.* / stage.<s>.start / stage.<s>.end / change", "—"),
    ("events", "on: stage.<s>.turn", "$actor $acted $timed_out"),
    ("events", "on: create.<t> / remove.<t>", "$it (the entity)"),
    ("entities", "brief", "$actor (generated: $row $i too)"),
    ("entities", "where/weight (generated)", "$row"),
    ("entities", "props/id/name (generated)", "$row $i ($i counts from 1)"),
    ("types", "inspect", "$viewer $it"),
    ("types", "policies.*.rules.*", "$actor ($it $i with `each`)"),
    ("relations", "props.*.default", "$from $to"),
    ("relations", "links.*.props", "$from $to (+ $row with `rows`)"),
    ("mechanisms", "physics.per.*.read/where (dynamics)", "$it"),
    ("mechanisms", "<feed>.query/when/fallback (host.feed)", "—"),
    ("defs", "expr", "the def's args"),
    ("defs", "do", "the def's args + locals"),
    ("outputs", "*", "$outputs (series outputs' latest samples; earlier outputs, except in a sampled one) $result "
                     "(winner, ended_by; not in a sampled one)"),
    ("end", "when/winner/say", "—"),
    ("types", "score.seat", "$it $i"),
    ("types", "score.value", "$it (the seat) $result (winner, ended_by)"),
    ("invariants", "*", "—"),
]

#: (section, models documented, shape, what it declares). The order is the guide's.
SECTIONS: list[tuple[str, list[type[BaseModel]], str, str]] = [
    ("brief", [C.Brief], "Brief",
     "Static text every agent reads first: the situation, the rules, and per-type role text."),
    ("clock", [C.Clock], "Clock", "How long a run lasts (`rounds`, default 20) and what one round is called."),
    ("inputs", [C.InputSpec], "{name: InputSpec}",
     "Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables, and the files the "
     "environment carries (`type: file`; see guide('assets'))."),
    ("world", [C.PropSpec], "{prop: default | PropSpec}", "Global properties ($world.x)."),
    ("types", [C.TypeSpec, C.PropSpec, C.PolicySpec, C.PolicyRule, C.ScoreSpec], "{type: TypeSpec}",
     "Kinds of entities and their properties; `agent: true` makes a type act, its `policies` are coded "
     "participants for its agents, for crowds and baselines (`policy:<name>`), and its `score` is what each of its "
     "agents scores as a seat, for tournaments, game search and gyms."),
    ("entities", [C.EntitySpec], "{id: EntitySpec}",
     "Named entities (the name defaults to the id), and generated ones: `count` of them, or one per data row "
     "(`from`), with sampled traits; ids `<key>_<n>`."),
    ("records", [C.RecordSpec], "{record: RecordSpec}",
     "Append-only logs (chat, reviews, bids) with per-viewer visibility; written with `post`."),
    ("actions", [C.ActionSpec, C.ParamSpec, C.Condition], "{action: ActionSpec}",
     "What agents can do: each is one typed tool with requirements and atomic effects. A rule that fails while an "
     "action applies (a division by zero, an overflow) refuses and undoes that action alone; the run goes on and its "
     "diagnostics name the rule."),
    ("stages", [C.StageSpec], "[StageSpec]",
     "The steps of every round: who acts, how (sequential or sealed simultaneous), which actions."),
    ("views", [C.ViewSpec], "{view: ViewSpec}", "What agents read each turn: single lines or ranked, filtered lists."),
    ("events", [C.EventSpec], "[EventSpec]",
     "What the world does outside agents' turns. `on` is when an event is considered — round.start (the default), "
     "round.end, stage.<s>.start, stage.<s>.end, stage.<s>.turn (after each agent's turn: $actor, $acted, "
     "$timed_out), create.<type>, remove.<type> ($it), or change (the moment `when` becomes true) — and `when` "
     "whether it fires: on given rounds (\"$round == 5\", \"$round % 7 == 1\"), in an arm (\"$arm == 't'\"), by "
     "chance. Events on one anchor fire in the order written, before those mechanisms generate."),
    ("end", [C.EndSpec], "[EndSpec]",
     "Conditions that end the run early, with an optional winner ($result.winner in outputs)."),
    ("outputs", [C.OutputSpec], "{output: expr | OutputSpec}",
     "The typed results of a run; with `series: true` also sampled every round ($outputs.x latest, $series.x every "
     "round)."),
    ("invariants", [C.InvariantSpec], "[expr | InvariantSpec]",
     "Rules that must always hold. An agent's action that breaks one is refused and undone (the `why` is its reason); "
     "a break by anything else fails the run."),
    ("mechanisms", [], "{name: {kind, mode, ...config}}",
     "Native building blocks by family (markets, voting, cards, roles, physics, patterns, feeds …): see "
     "guide('mechanisms')."),
    ("space", [C.Space, C.GridSpace, C.GraphSpace, C.PlaneSpace, C.LayerSpec], "Space",
     "Positions: a grid, a graph of places or a plane, with values on cells."),
    ("relations", [C.RelationSpec, C.LinkSpec], "{relation: RelationSpec}",
     "Typed links between entities (trust, follows), with fields, and the links made at build (`links`): listed, "
     "from data rows, or generated networks."),
    ("arms", [C.ArmSpec], "{arm: ArmSpec}", "Experiment variants: input overrides or contract patches."),
    ("defs", [C.DefSpec], "{name: expr | DefSpec}",
     "Reusable expressions, called like built-ins ($utility($actor, 3)), and effect lists (`do`), run with "
     "{\"call\": name, \"with\": {...}}."),
    ("imports", [], "[path]",
     "Contract files merged into this one (relative to it, inside its folder); this contract's own entries win, and "
     "imported files may import others."),
]

#: The core language: the sections and functions the start page (``guide('authoring')``) teaches, enough for most
#: environments. Every other section and function is extended: reach for one when the core cannot say it.
CORE_SECTIONS = ("brief", "clock", "inputs", "world", "types", "entities", "records", "actions", "stages",
                 "views", "events", "end", "outputs", "invariants")

_SECTION_INDEX = {name: (models, shape, doc) for name, models, shape, doc in SECTIONS}


def _roots_rows(section: str | None = None) -> list[str]:
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
    "collections": "count sum avg min max median quantile stdev top sort best filter map pick any all len first last "
                   "unique tally mode reverse slice range flatten dict keys values get is",
    "world": "entity records events seen asset",
    "space": "relation linked link links neighbors distance",
    "math": "abs floor ceil sqrt exp log round clamp pct",
    "random": "chance uniform randint normal lognormal beta exponential poisson choice sample shuffle",
    "text": "text lower contains join fmt",
}
_MODULE_GROUPS = {
    "stdlib.mathx": "math", "stdlib.linalg": "random", "stdlib.dists": "random", "stdlib.strings": "text",
    "stdlib.words": "words", "stdlib.dates": "dates", "stdlib.lists": "lists", "stdlib.tables": "lists",
    "stdlib.sets": "lists", "stdlib.stats": "stats",
    "stdlib.scoring": "stats", "stdlib.space": "space", "world.networks": "space", "stdlib.puzzles": "words",
    "mechanisms.market_stats": "stats", "mechanisms.voting": "stats",
}
#: What each non-family group holds, in the order the guide lists them.
FUNCTION_GROUPS = {
    "collections": "counting, summing, ranking and filtering lists and entity types",
    "world": "entities, records, events and what agents were shown",
    "math": "arithmetic, trigonometry, interpolation",
    "random": "seeded draws and distributions",
    "text": "text and formatting",
    "dates": "calendar arithmetic and parts of ISO dates",
    "lists": "list and map manipulation, sets",
    "stats": "statistics, time series and forecast scores",
    "space": "grids, graphs, networks and links",
    "words": "word games and puzzles: dictionaries, anagrams, crosswords, sudoku",
}
_OTHER = "other"


def _group(spec: FunctionSpec) -> str:
    if spec.families:  # a mechanism's function: on its (first) family's page
        return spec.families[0]
    for group, names in _CORE_GROUPS.items():
        if spec.name in names.split():
            return group
    return _MODULE_GROUPS.get(spec.impl.__module__.removeprefix("fg_env."), _OTHER)


def function_groups() -> dict[str, list[FunctionSpec]]:
    """Every registered function by group: the general groups first, then mechanism families."""
    out: dict[str, list[FunctionSpec]] = {group: [] for group in [*FUNCTION_GROUPS, *FAMILIES, _OTHER]}
    for spec in sorted(FUNCTIONS.values(), key=lambda s: s.name):
        out[_group(spec)].append(spec)
    return {group: specs for group, specs in out.items() if specs}


def functions_index() -> str:
    lines = ["## Functions", "", "Every function by group. Read one group's signatures and docs with "
             "`guide('functions.<group>')`.", "",
             "Core (the start page teaches them): " + " ".join(f"${name}" for name in CORE_FUNCTIONS)
             + ". Every other function is extended.", "", "General functions, for any contract:", ""]
    groups = function_groups()
    for group in [g for g in groups if g in FUNCTION_GROUPS]:
        lines.append(f"- `{group}` ({FUNCTION_GROUPS[group]}): " + " ".join(f"${s.name}" for s in groups[group]))
    other = groups.get(_OTHER)
    if other:
        lines.append(f"- `{_OTHER}`: " + " ".join(f"${s.name}" for s in other))
    families = [g for g in groups if g in FAMILIES]
    lines += ["", "A mechanism's functions read its state (a board, a deck, a market …) and can be called only in a "
                  "contract that declares a mechanism of its family; each family's page lists them: "
              + ", ".join(f"`guide('{g}')`" for g in families) + "."]
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
    from ..mechanisms.families import SHARED

    lines = ["## Mechanisms (native building blocks)", "",
             "Declare `\"mechanisms\": {name: {\"kind\": <family>, \"mode\": <mode>, ...config}}`. Each expands into",
             "ordinary actions, stages, world props and events you can read, preview and override (declare the same",
             "name yourself to replace a generated part, a named event or end entry included, but not a world",
             "property: that is the mechanism's state; two mechanisms generating one name is an error). A declared "
             "stage that offers only mechanisms' actions and sets no",
             "`max_actions` gives each attached mechanism the actions per turn it has in its own stage. Combine",
             "them freely, several of one mode included: a function reading a mechanism takes its name as the last",
             "argument (`$decisions('committee')`), optional while the contract has only one of that mode. Only a",
             "mechanism made to end the run does (a board's game over, a terminal phase, a deliberation with",
             "`end`): `fg-env check` lists what each one generated, and which can end the run, and",
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
        lines += (["Named the same in every mode:"]
                  + [f"- `{key}`: {meaning}" for key, meaning in family.shared.items()] + [""])
    lines += [f"Modes (`\"kind\": \"{name}\", \"mode\": ...`; read one with `guide('{name}.<mode>')`):"]
    lines += [f"- `{mode}`: {spec.doc.split('. ')[0].rstrip('.')}." for mode, spec in family.modes.items()]
    specs = function_groups().get(name, [])
    if specs:
        lines += ["", "Functions:", *[f"- `${spec.signature}` — {spec.doc}" for spec in specs]]
    return "\n".join(lines)


def mode_page(spec: ModeSpec) -> str:
    lines = [f"### `{spec.key}`", spec.doc, "", "Config:"]
    for field_name, info in spec.config.model_fields.items():
        if field_name in ("kind", "mode"):  # the entry's own kind and mode, above
            continue
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


def _public(family: FamilySpec, mode: str) -> list[str]:
    """The actions of a mode an author writes (the mechanism's own bookkeeping actions left out)."""
    return [action for action, op in family.actions.get(mode, {}).items() if not op.internal]


# -- model fields ---------------------------------------------------------------

def _type_name(annotation: Any, field: str) -> str:
    if field == "do":
        return "effects"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        names = [_type_name(a, field) for a in args if a is not type(None)]
        return " | ".join(dict.fromkeys(names))
    if origin is list:
        return f"[{_type_name(args[0], field)}]" if args else "list"
    if origin is dict:
        return "object"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__
    return {str: "text", int: "int", float: "number", bool: "bool"}.get(annotation, "any")


def _fields(model: type[BaseModel]) -> str:
    lines = [f"**{model.__name__}** — {inspect.cleandoc(model.__doc__ or '')}"]
    for name, info in model.model_fields.items():
        key = info.alias or name
        required = info.is_required()
        plain = info.default is PydanticUndefined or info.default in (None, "", [], {})
        default = "" if required or plain else f" = {json.dumps(info.default, default=str)}"
        flag = " (required)" if required else ""
        description = f" — {info.description}" if info.description else ""
        lines.append(f"- `{key}`: {_type_name(info.annotation, name)}{default}{flag}{description}")
    return "\n".join(lines)


def _nested_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    """Models used inside ``model``'s fields (in lists, maps and optionals too), each once, depth first."""
    found: list[type[BaseModel]] = []

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
