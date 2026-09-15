"""The authoring guide, generated from the SDK itself.

``fg_env.guide()`` returns the core guide: enough to write a first environment, with a map of every other part.
``fg_env.guide("actions")`` returns one part, ``fg_env.guide("all")`` everything. Field references, functions,
effects and mechanisms come straight from the code, so the guide cannot drift from what the engine accepts.
"""
from __future__ import annotations

from difflib import get_close_matches
from typing import Any, Callable, Dict, List, Optional

from . import contract as C
from .guide_pages import (SECTIONS, effects_page, expressions_page, family_page, function_groups, functions_index,
                          functions_page, mechanisms_page, mode_page, section_page)
from .guide_text import CHECKLIST, MACROS, MODEL, PATTERNS, RUNNING, TEMPLATES
from .macros import MAX_MACRO_DEPTH, MAX_MACRO_ITEMS
from .registry import FAMILIES
from .template import FORMATS

__all__ = ["guide", "schema", "guide_parts", "QUICKSTART"]


def schema() -> Dict[str, Any]:
    """JSON Schema of the contract (structure only; ``fg_env.check`` verifies meaning)."""
    return C.Contract.model_json_schema(by_alias=True)


QUICKSTART = """\
{
  "name": "Coin flip",
  "brief": {"rules": "Bet some coins each round. Heads you win that much, tails you lose it."},
  "types": {"player": {"agent": true, "props": {"coins": 10}}},
  "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}},
  "actions": {"bet": {"by": "player", "params": {"amount": {"type": "int", "min": 1, "max": "$actor.coins"}},
                      "do": "$actor.coins += $params.amount if $chance(0.5) else -$params.amount"}},
  "outputs": {"richest": "$best(player, $it.coins, 'random').name"}
}"""

_CORE = """\
# fg_env — core guide

An environment is one JSON contract; the engine runs it. You declare what exists (`types` of entities with
properties, and the `entities` themselves), what agents can do (`actions`: typed parameters, requirements, effects),
when they act (`stages`), what they see (`views`) and what is measured (`outputs`). Each turn the engine gives an
agent a brief, an update and one typed tool per action it can take right now, applies what it does atomically, runs
the world's `events`, and at the end returns typed outputs. You write data, never code.

Workflow: `fg-env new game my_game.json` (or write one) → `fg-env check my_game.json` (static checks plus one played
round; fix every issue) → `fg-env preview my_game.json <agent id>` (exactly what that agent reads) →
`fg-env run my_game.json --seed 1`. In Python: `fg_env.new`, `fg_env.check`, `env.preview`, `fg_env.run`.

## Quickstart

```json
QUICKSTART
```

`fg_env.run(contract, seed=1)` plays it with random agents; `fg_env.run(contract, {"player": my_llm})` with yours.
Defaults at work: 20 rounds (`clock.rounds`); one stage where agents act one after another, one action per turn;
names from ids; a tool is offered only while it can be used (at 0 coins `bet` has no valid amount).

## How a round runs

Start events → each stage in order → end events → metrics → `end` conditions. A run ends when an `end` condition
holds, an `end` effect runs, or the rounds are used up. `end` conditions are checked after each stage; with
`"check": "action"` also the moment any action commits, so a winning move ends the run before anyone else moves:
`{"when": "$world.found", "winner": "$world.finder", "check": "action"}`.
* A stage wakes agents (`who`, in `order`). `turns: sequential` — one at a time, actions apply at once and the tool
  result is the outcome. `turns: simultaneous` — everyone chooses from the same picture, then choices commit together
  (sealed bids, votes); outcomes arrive as news.
* A turn ends after `max_actions` actions (default 1), when the agent calls `end_turn`, or after `max_calls` calls.
  `terminal: true` ends it early when a stage allows more than one action.
* An action is atomic: if an effect `fail`s or a `transfer` lacks funds, all of it is undone and the agent is told why.
* Others read "Name: action (args)." unless the action is `private` or sets `announce`. Text an agent writes is
  always shown «quoted».

## Sections

Every section is optional except `name` and `types`. Read any one with `guide('<section>')`.

| section | shape and main fields |
|---|---|
| `brief` | `{situation, rules, roles: {type: text}}` — templates |
| `clock` | `{rounds: 20, unit: "round"}` |
| `inputs` | `{name: {type, default}}` — knobs set at load, read as `$inputs.name` |
| `world` | `{prop: default}` — global props, `$world.prop` |
| `types` | `{type: {agent, props: {prop: default or {type, default, min, max, values, private}}, extends}}` |
| `entities` | `{id: {type, name, props}}` |
| `population` | `[{type, count, name: "Buyer {$i}", props}]` |
| `records` | `{log: {fields: {text: "text"}, show: "{author}: {text}", visible}}` — written by `post` |
| `actions` | `{act: {by, description, params: {p: {type, min, max, values, of, where}}, when, do, outcome, announce, private}}` |
| `stages` | `[{name, actions, turns, who, order, max_actions, until, on_enter, on_exit}]` |
| `views` | `{v: {for, title, of, where, sort, desc, limit, show}}` — `of` omitted: one line about `$actor` |
| `events` | `[{phase: start or end, at, every, when, chance, each, do, say}]` |
| `end` | `[{when, winner, say, check: stage or action}]` |
| `metrics`, `outputs` | `{name: expr}` or `{name: {expr, type}}`; an output's `format` (money, pct, 2 …) shapes how summaries show it |
| `invariants` | `[expr]` |
| `mechanisms` | `{name: {kind, mode, ...config}}` — see the families below |
| `game` | `{players, returns}` — seats and scores for tournaments and game search |

Advanced sections, each in its own part: `triggers` (effects the moment a condition becomes true), `space`,
`relations`, `links`, `physics`, `feeds`, `policies`, `arms`, `defs`, `blocks`, `imports`.

Property and input types: number int bool text enum list map any (inferred from the default; `integer`, `string`,
`boolean` and `float` also work). Parameter types: number int bool text enum entity list. An `entity` parameter
names its type in `of` and may filter with `where` (`$it` the candidate); its tool lists the valid ids.

## Expressions

A string with `$name` in it is an expression; other strings are text.
* Roots: `$actor` (who acts), `$params` (its arguments), `$it` (the current item), `$world`, `$inputs`, `$round`,
  `$clock`, `$metrics`; locals you assign (`$total`). Props: `$actor.coins`, `$params.target.name`,
  `$entity(shop).stock`. Entities also have `id name type alive`.
* Operators: `+ - * / // % **`, `== != < <= > >=`, `and or not`, `in`, `a if cond else b`, lists `[1, 2]`, maps
  `{price: 3}`, indexing `$list[0]`. Bare words are text: `$actor.role == wolf`. Compare with `==`, never `=`.
* Functions always take `$`: `$count(buyer, $it.cash > 0)`, `$sum(player, $it.coins)`, `$avg`, `$min`, `$max`,
  `$filter(player, $it.alive)`, `$map(player, $it.name)`, `$top(offer, $it.price, 3)`, `$best(player, $it.score)`,
  `$any`, `$all`, `$len`, `$chance(0.3)`, `$randint(1, 6)`, `$choice(list)`, `$shuffle(list)`, `$round(x, 2)`.
  Per-item arguments read `$it` (and `$i`).
* `$result.winner` (in outputs and game returns) is the winner an `end` gave; `$result.ended_by` the end's name.
* Strict: unknown props, missing roots and type errors are reported with the fix, never silent zeros.

Where the roots come from: `actions.when` $actor (and $params, checked when called) · params `where` $actor $it
$params · `do`/`outcome`/`announce` $actor $params · stages `who`/`order` $it · views `where`/`sort`/`show` $actor $it ·
`events` with `each` $it · outputs $outputs $result. Each section's page has its full table.

Templates (`show`, `outcome`, `announce`, `say`, `brief`, `name`): `"{name} has {coins} coins"` reads the subject
(`$it` in lists, `$actor` otherwise); `{$params.amount|money}` any expression with a format (FORMATS).

## Effects

`do` (in actions, events, stages' `on_enter`/`on_exit`) is a list of effects, or one effect on its own:
* Assignments: `"$actor.coins -= $params.amount"`, `"$world.pot += 5"`, `"$total = $params.qty * 2"` (a local);
  also `=`, `+=`, `-=`, `*=`, `/=`; `+=` on a list appends.
* `{"if": "$world.stock < $params.qty", "then": [{"fail": "Not enough stock."}], "else": [...]}`
* `{"each": "player", "where": "$it.coins == 0", "do": ["$it.alive_rounds = 0"]}`
* `{"transfer": "coins", "from": "$actor", "to": "$params.target", "amount": 3}` — fails the action if short
* `{"create": "order", "props": {"price": "$params.price"}}` · `{"remove": "$params.order"}`
* `{"post": "chat", "text": "$params.text"}` · `{"emit": "storm", "say": "A storm hits."}`
* `{"fail": "You cannot afford that."}` — undo the action and tell the actor
* `{"end": "bankrupt", "winner": "$best(player, $it.coins)", "say": "{$actor.name} went broke."}`
* `{"after": 2, "do": [...]}` — later, with the same locals

An action's `when` holds requirements: `["$actor.coins > 0", {"expr": "$params.amount <= $world.cap", "why": "Too
much."}]`. Requirements over `$actor` decide whether the tool is offered; ones that read `$params` refuse a call
with their `why`.

## Mechanisms

Native building blocks expand into ordinary actions, stages, views and outputs: `"mechanisms": {"sale": {"kind":
"market", "mode": "auction", "format": "first_price", "who": "bidder"}}`. `fg-env check` lists what each generated.

FAMILIES

## Every other part

Read with `fg_env.guide('<part>')` or `fg-env guide <part>`; `guide('all')` is everything.
PARTS
"""

_PARTS_MAP = [
    ("<section>", "one section's fields and roots: " + ", ".join(f"`{name}`" for name, *_ in SECTIONS)),
    ("model", "how a run works in detail: turns, atomic turns, time limits, hooks, invariants, what an agent reads"),
    ("expressions", "the expression language in full, with every root by location"),
    ("templates", "templates and formats"),
    ("effects", "every effect op with an example"),
    ("functions", "every function by group; `functions.<group>` for one group's docs (e.g. `functions.stats`)"),
    ("mechanisms", "the family table and names every family shares; `<family>` and `<family>.<mode>` "
                   "(e.g. `market`, `market.auction`)"),
    ("patterns", "recipes: data files, continuous time, markets, hidden roles, spaces, networks, physics, feeds"),
    ("macros", "repeat structure from data with `for`/`make`"),
    ("running", "Python API: participants, runs, snapshots, experiments, traces, evaluation, games, gyms, CLI"),
    ("checklist", "what makes an environment great for LLM agents"),
]


def _core() -> str:
    families = ["| kind | modes | for |", "|---|---|---|"]
    families += [f"| `{name}` | {', '.join(family.modes) or '—'} | {family.doc} |" for name, family in FAMILIES.items()]
    parts = "\n".join(f"- `{name}` — {about}" for name, about in _PARTS_MAP)
    return (_CORE.replace("QUICKSTART", QUICKSTART).replace("FORMATS", ", ".join(FORMATS))
            .replace("FAMILIES", "\n".join(families)).replace("PARTS", parts))


def _game_page() -> str:
    """`game` names both a contract section and a mechanism family; both are about games, so they share a page."""
    return family_page("game") + "\n\n" + section_page("game")


_TOPICS: Dict[str, Callable[[], str]] = {
    "core": _core,
    "model": lambda: MODEL,
    "expressions": expressions_page,
    "templates": lambda: TEMPLATES.replace("FORMATS", ", ".join(f"`{f}`" for f in FORMATS)),
    "effects": effects_page,
    "functions": functions_index,
    "mechanisms": mechanisms_page,
    "patterns": lambda: PATTERNS,
    "macros": lambda: MACROS.replace("MAX_ITEMS", f"{MAX_MACRO_ITEMS:,}").replace("MAX_DEPTH", str(MAX_MACRO_DEPTH)),
    "running": lambda: RUNNING,
    "checklist": lambda: CHECKLIST,
}


def guide_parts() -> List[str]:
    """Every name ``guide`` accepts, in the order ``guide('all')`` renders them (``all`` itself last)."""
    sections = [name for name, *_ in SECTIONS if name not in _TOPICS and name not in FAMILIES]
    names = ["core", "model", *sections, "expressions", "templates", "effects", "functions"]
    names += [f"functions.{group}" for group in function_groups() if group not in FAMILIES]
    names += ["macros", "patterns", "mechanisms"]
    for name, family in FAMILIES.items():
        names += [name, *[spec.key for spec in family.modes.values()]]
    names += [f"functions.{group}" for group in function_groups() if group in FAMILIES]
    return [*names, "running", "checklist", "all"]


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
    ``"functions"``, ``"mechanisms"``, ``"patterns"``, ``"running"`` …), a function group (``"functions.stats"``),
    a mechanism family (``"market"``) or mode (``"market.auction"``) — or ``"all"`` for everything.
    The core guide ends with a map of the parts."""
    if part is None:
        return _core()
    if part == "all":
        # Family function groups are already on their family pages.
        return "\n\n".join(_render(name) or "" for name in guide_parts()[:-1]
                           if not (name.startswith("functions.") and name.partition(".")[2] in FAMILIES))
    rendered = _render(part)
    if rendered is None:
        hint = get_close_matches(part, guide_parts(), n=1, cutoff=0.6)
        suggestion = f"did you mean '{hint[0]}'? " if hint else ""
        raise KeyError(f"unknown guide part '{part}' → {suggestion}guide() ends with a map of every part")
    return rendered
