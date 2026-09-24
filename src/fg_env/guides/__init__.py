"""The authoring guide, generated from the SDK itself.

``fg_env.guide("authoring")`` is the start page: enough to write a first environment, and an authoring agent's
starting context. ``fg_env.guide()`` is the map: where every section, mechanism and topic is explained.
``fg_env.guide("actions")`` returns one part, ``fg_env.guide("all")`` everything (a human's reference, far too long
for an agent's context). Field references, functions, effects and mechanisms come straight from the code, so the guide
cannot drift from what the engine accepts.
"""
from __future__ import annotations

from collections.abc import Callable
from difflib import get_close_matches
from typing import Any

from .. import contract as C
from ..analysis.optimise_guide import OPTIMISE
from ..assets.guide import ASSETS
from ..authoring.scaffold import TEMPLATES as STARTING_TEMPLATES
from ..contract.macros import MAX_MACRO_DEPTH, MAX_MACRO_ITEMS
from ..engines import list_engines
from ..expr.template import FORMATS
from ..patterns.guide import patterns_page
from ..registry import FAMILIES
from .authoring import AUTHORING
from .pages import (
    CORE_FUNCTIONS,
    CORE_SECTIONS,
    SECTIONS,
    effects_page,
    expressions_page,
    family_page,
    function_groups,
    functions_index,
    functions_page,
    mechanisms_page,
    mode_page,
    section_page,
)
from .text import CHECKLIST, INSPECT, MACROS, MODEL, RECIPES, RUNNING, TEMPLATES

__all__ = ["guide", "schema", "guide_parts"]


def schema() -> dict[str, Any]:
    """JSON Schema of the contract (structure only; ``fg_env.check`` verifies meaning)."""
    out = C.Contract.model_json_schema(by_alias=True)
    for name, _, _, doc in SECTIONS:
        field = out["properties"][name]
        tier = "Core" if name in CORE_SECTIONS else "Extended"
        field["description"] = f"{tier} section. {field.get('description') or _first_sentence(doc)}"
    return out


_MAP = """\
# fg_env guide — the map

Start with `guide('authoring')` (`fg-env guide authoring`): one page with a complete worked contract, the
write → check → preview → run loop and the core language. It is enough for a first environment; read the parts
below only when you need them. To have a model do the loop for you: `fg-env author brief.md --model
anthropic:<model>` (`fg_env.author`); it keeps the best contract that checks without errors and plays soundly.

## Core sections

Every section is optional except `name` and `types`; `guide('<section>')` has its fields and the roots available in
each. The core sections and functions are enough for most environments; the start page teaches them.

CORE

Core functions: FUNCTIONS; every other function (`guide('functions')`) is extended.

## Extended sections

Reach for one of these when the core cannot say it.

EXTENDED

## Mechanism, engine or template?

A mechanism (`market`, `decision` …) is a building block inside your contract. An engine (`retail`, `council` …) is a
complete contract to copy and edit: `fg-env new --engine <id>`. A starting template is a small contract to start from:
`fg-env new <template>` with TEMPLATES.

## Mechanisms

Ready-made rules that expand into ordinary actions, stages, views and outputs:
`"mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder"}}`.

FAMILIES

## Engines

Runnable starters, one per kind of human interaction: each a complete contract with coded participants that runs as
cloned, to copy and make your own (topic, roles, people, inputs, rules) rather than start blank:
`fg-env new --engine <id> my_env.json` (`fg_env.engines.clone`); `fg-env engines` lists them.

ENGINES

## Every other part

`fg_env.guide('<part>')` or `fg-env guide <part>`:
PARTS
"""

_PARTS_MAP = [
    ("authoring", "the start page: a worked contract, the loop and the core language; read it first"),
    ("model", "how a run works in detail: turns, time limits, hooks, invariants, what an agent reads"),
    ("expressions", "the expression language in full, with every root by location"),
    ("templates", "templates and formats"),
    ("effects", "every effect op with an example"),
    ("functions", "every function by group; `functions.<group>` for one group (e.g. `functions.stats`)"),
    ("mechanisms", "what every family shares; `<family>` and `<family>.<mode>` (e.g. `market.auction`)"),
    ("patterns", "seasons, trends, responses, random processes, draws and noise, and fitting them from data"),
    ("recipes", "data files, queues, markets, hidden roles, spaces, networks, physics, feeds"),
    ("macros", "repeat structure from data with `for`/`make`"),
    ("assets", "files beside the contract (images, PDFs, text) delivered to agents"),
    ("inspect", "debugging a run: summary, diagnostics, events, traces, replay"),
    ("running", "Python API: participants, runs, snapshots, experiments, traces, evaluation, games, gyms, CLI"),
    ("optimise", "the best decision under constraints: objectives, methods, fresh-seed checks, Pareto frontiers"),
    ("checklist", "what makes an environment great for LLM agents"),
]


def _first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".") + "."


def _core() -> str:
    header = ["| section | what it declares |", "|---|---|"]
    core = header + [f"| `{name}` | {_first_sentence(doc)} |" for name, _, _, doc in SECTIONS if name in CORE_SECTIONS]
    extended = header + [f"| `{name}` | {_first_sentence(doc)} |" for name, _, _, doc in SECTIONS
                         if name not in CORE_SECTIONS]
    families = ["| kind | modes | for |", "|---|---|---|"]
    families += [f"| `{name}` | {', '.join(family.modes) or '—'} | {family.doc} |" for name, family in FAMILIES.items()]
    parts = "\n".join(f"- `{name}` — {about}" for name, about in _PARTS_MAP)
    engines = "\n".join(f"- `{engine.id}` — {engine.summary}" for engine in list_engines(available=True))
    functions = " ".join(f"`${name}`" for name in CORE_FUNCTIONS)
    return (_MAP.replace("CORE", "\n".join(core)).replace("EXTENDED", "\n".join(extended))
            .replace("FUNCTIONS", functions).replace("TEMPLATES", ", ".join(f"`{name}`" for name in STARTING_TEMPLATES))
            .replace("FAMILIES", "\n".join(families))
            .replace("ENGINES", engines).replace("PARTS", parts))


def _game_page() -> str:
    """`game` names both a contract section and a mechanism family; both are about games, so they share a page."""
    return family_page("game") + "\n\n" + section_page("game")


_TOPICS: dict[str, Callable[[], str]] = {
    "core": _core,
    "authoring": lambda: AUTHORING,
    "model": lambda: MODEL,
    "expressions": expressions_page,
    "templates": lambda: TEMPLATES.replace("FORMATS", ", ".join(f"`{f}`" for f in FORMATS)),
    "effects": effects_page,
    "functions": functions_index,
    "mechanisms": mechanisms_page,
    "patterns": patterns_page,
    "recipes": lambda: RECIPES,
    "macros": lambda: MACROS.replace("MAX_ITEMS", f"{MAX_MACRO_ITEMS:,}").replace("MAX_DEPTH", str(MAX_MACRO_DEPTH)),
    "inspect": lambda: INSPECT,
    "running": lambda: RUNNING,
    "optimise": lambda: OPTIMISE,
    "assets": lambda: section_page("assets") + "\n\n" + ASSETS,
    "checklist": lambda: CHECKLIST,
}


def guide_parts() -> list[str]:
    """Every name ``guide`` accepts, in the order ``guide('all')`` renders them (``all`` itself last)."""
    sections = [name for name, *_ in SECTIONS if name not in _TOPICS and name not in FAMILIES]
    names = ["core", "authoring", "model", *sections, "assets", "expressions", "templates", "effects", "functions"]
    names += [f"functions.{group}" for group in function_groups() if group not in FAMILIES]
    names += ["patterns", "macros", "recipes", "mechanisms"]
    for name, family in FAMILIES.items():
        names += [name, *[spec.key for spec in family.modes.values()]]
    names += [f"functions.{group}" for group in function_groups() if group in FAMILIES]
    return [*names, "inspect", "running", "optimise", "checklist", "all"]


def _render(part: str) -> str | None:
    if part in _TOPICS:
        return _TOPICS[part]()
    if part == "game":
        return _game_page()
    if part in FAMILIES:
        return family_page(part)
    if any(part == name for name, *_ in SECTIONS):
        return section_page(part)
    head, _, rest = part.partition(".")
    if head == "functions" and rest in function_groups():
        return functions_page(rest)
    if head in FAMILIES and rest in FAMILIES[head].modes:
        return mode_page(FAMILIES[head].modes[rest])
    return None


def guide(part: str | None = None) -> str:
    """The map of every part, or one part by name: a section (``"actions"``), a topic (``"expressions"``, ``"effects"``,
    ``"functions"``, ``"mechanisms"``, ``"patterns"``, ``"recipes"``, ``"running"`` …), a function group
    (``"functions.stats"``), a mechanism family (``"market"``) or mode (``"market.auction"``) — or ``"all"`` for
    everything. With no part, the map of every part; start with ``guide("authoring")``."""
    if part is None:
        return _core()
    if part == "all":
        # Family function groups are already on their family pages.
        return "\n\n".join(_render(name) or "" for name in guide_parts()[:-1]
                           if not (name.startswith("functions.") and name.partition(".")[2] in FAMILIES))
    rendered = _render(part)
    if rendered is None:
        # A bare function-group name is a better match than an unrelated
        # fuzzy topic (for example, math previously suggested market).
        hint = ([f"functions.{part}"] if part in function_groups()
                else get_close_matches(part, guide_parts(), n=1, cutoff=0.6))
        suggestion = f"did you mean '{hint[0]}'? " if hint else ""
        raise KeyError(f"unknown guide part '{part}' → {suggestion}guide() maps every part")
    return rendered
