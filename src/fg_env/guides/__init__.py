"""The authoring guide, generated from the SDK itself.

``fg_env.guide()`` returns the core guide: the start page (enough to write a first environment) with a map of every
other part. ``fg_env.guide("authoring")`` is the start page alone, for an authoring agent's starting context;
``fg_env.guide("actions")`` returns one part, ``fg_env.guide("all")`` everything (a human's reference, far too long
for an agent's context). Field references, functions, effects and mechanisms come straight from the code, so the guide
cannot drift from what the engine accepts.
"""
from __future__ import annotations

from difflib import get_close_matches
from typing import Any, Callable, Dict, List, Optional

from .. import contract as C
from .authoring import AUTHORING, START
from .pages import (SECTIONS, effects_page, expressions_page, family_page, function_groups, functions_index,
                    functions_page, mechanisms_page, mode_page, section_page)
from ..analysis.optimise_guide import OPTIMISE
from ..assets.guide import ASSETS
from .text import CHECKLIST, INSPECT, MACROS, MODEL, RECIPES, RUNNING, TEMPLATES
from ..patterns.guide import patterns_page
from ..patterns.schema import patterns_definitions, patterns_field_schema
from ..macros import MAX_MACRO_DEPTH, MAX_MACRO_ITEMS
from ..registry import FAMILIES
from ..template import FORMATS

__all__ = ["guide", "schema", "guide_parts"]


def schema() -> Dict[str, Any]:
    """JSON Schema of the contract (structure only; ``fg_env.check`` verifies meaning)."""
    out = C.Contract.model_json_schema(by_alias=True)
    out["$defs"] = {**out.get("$defs", {}), **patterns_definitions()}
    out["properties"]["patterns"] = {**out["properties"]["patterns"], **patterns_field_schema()}
    return out


_CORE_TAIL = """
## Mechanisms

Ready-made rules that expand into ordinary actions, stages, views and outputs:
`"mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder"}}`.

FAMILIES

To have a model do the write → check → preview → run loop for you: `fg-env author brief.md --model anthropic:<model>`
(`fg_env.author`); it keeps the latest contract that checks clean and runs.

## Every other part

`fg_env.guide('<part>')` or `fg-env guide <part>`:
PARTS
"""

_PARTS_MAP = [
    ("authoring", "the start page above with a short reading list: an authoring agent's starting context"),
    ("<section>", "any section above: its fields and the roots available in each"),
    ("model", "how a run works in detail: turns, time limits, hooks, invariants, what an agent reads"),
    ("expressions", "the expression language in full, with every root by location"),
    ("templates", "templates and formats"),
    ("effects", "every effect op with an example"),
    ("functions", "every function by group; `functions.<group>` for one group (e.g. `functions.stats`)"),
    ("mechanisms", "what every family shares; `<family>` and `<family>.<mode>` (e.g. `market.auction`)"),
    ("patterns", "seasons, trends, responses, random processes, draws and noise, and fitting them from data"),
    ("recipes", "data files, continuous time, markets, hidden roles, spaces, networks, physics, feeds"),
    ("macros", "repeat structure from data with `for`/`make`"),
    ("inspect", "debugging a run: summary, diagnostics, events, traces, replay"),
    ("running", "Python API: participants, runs, snapshots, experiments, traces, evaluation, games, gyms, CLI"),
    ("optimise", "the best decision under constraints: objectives, methods, fresh-seed checks, Pareto frontiers"),
    ("checklist", "what makes an environment great for LLM agents"),
]


def _core() -> str:
    families = ["| kind | modes | for |", "|---|---|---|"]
    families += [f"| `{name}` | {', '.join(family.modes) or '—'} | {family.doc} |" for name, family in FAMILIES.items()]
    parts = "\n".join(f"- `{name}` — {about}" for name, about in _PARTS_MAP)
    return START + _CORE_TAIL.replace("FAMILIES", "\n".join(families)).replace("PARTS", parts)


def _game_page() -> str:
    """`game` names both a contract section and a mechanism family; both are about games, so they share a page."""
    return family_page("game") + "\n\n" + section_page("game")


_TOPICS: Dict[str, Callable[[], str]] = {
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


def guide_parts() -> List[str]:
    """Every name ``guide`` accepts, in the order ``guide('all')`` renders them (``all`` itself last)."""
    sections = [name for name, *_ in SECTIONS if name not in _TOPICS and name not in FAMILIES]
    names = ["core", "authoring", "model", *sections, "assets", "expressions", "templates", "effects", "functions"]
    names += [f"functions.{group}" for group in function_groups() if group not in FAMILIES]
    names += ["patterns", "macros", "recipes", "mechanisms"]
    for name, family in FAMILIES.items():
        names += [name, *[spec.key for spec in family.modes.values()]]
    names += [f"functions.{group}" for group in function_groups() if group in FAMILIES]
    return [*names, "inspect", "running", "optimise", "checklist", "all"]


def _render(part: str) -> Optional[str]:
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


def guide(part: Optional[str] = None) -> str:
    """The core guide, or one part by name: a section (``"actions"``), a topic (``"expressions"``, ``"effects"``,
    ``"functions"``, ``"mechanisms"``, ``"patterns"``, ``"recipes"``, ``"running"`` …), a function group (``"functions.stats"``),
    a mechanism family (``"market"``) or mode (``"market.auction"``) — or ``"all"`` for everything.
    The core guide ends with a map of the parts."""
    if part is None:
        return _core()
    if part == "all":
        # The start page (`authoring`) is already in the core guide, family function groups on their family pages.
        return "\n\n".join(_render(name) or "" for name in guide_parts()[:-1] if name != "authoring"
                           and not (name.startswith("functions.") and name.partition(".")[2] in FAMILIES))
    rendered = _render(part)
    if rendered is None:
        # A bare function-group name is a better match than an unrelated
        # fuzzy topic (for example, math previously suggested market).
        hint = ([f"functions.{part}"] if part in function_groups()
                else get_close_matches(part, guide_parts(), n=1, cutoff=0.6))
        suggestion = f"did you mean '{hint[0]}'? " if hint else ""
        raise KeyError(f"unknown guide part '{part}' → {suggestion}guide() ends with a map of every part")
    return rendered
