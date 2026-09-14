"""The authoring guide — everything needed to write a contract, generated from the SDK itself.

``fg_env.guide()`` returns the whole guide; ``fg_env.guide("effects")`` one part. Field
references, functions, effects and formats come straight from the code, so the guide
cannot drift from what the engine accepts.
"""
from __future__ import annotations

import json
import typing
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from . import contract as C
from .effects import EFFECT_OPS
from .registry import MECHANISMS, OPS
from .expr import FUNCTIONS
from .template import FORMATS

__all__ = ["guide", "schema", "GUIDE_PARTS"]


def schema() -> Dict[str, Any]:
    """JSON Schema of the contract (structure only; ``fg_env.check`` verifies meaning)."""
    return C.Contract.model_json_schema(by_alias=True)


_OVERVIEW = """\
# fg_env — authoring guide

An environment is ONE JSON contract. The engine builds the world, wakes agents, gives each a
short plain-language picture plus typed tools, applies their actions atomically, runs world
events and physics, measures, and returns typed outputs. You write data, never code.

Workflow: write the contract → `fg_env.check(contract)` (or `fg-env check file.json`) and fix
every issue it lists → `fg-env preview file.json <agent_id>` to read exactly what an agent sees
→ `fg-env run file.json --seed 1` → iterate.

Minimal complete contract:

```json
{
  "name": "Lemonade stand",
  "brief": {"situation": "Two kids sell lemonade on a hot day.",
            "rules": "Set your price each round. Cheaper stands get more customers."},
  "clock": {"rounds": 5, "unit": "hour"},
  "types": {"seller": {"agent": true, "props": {"price": 1.0, "earned": 0}}},
  "entities": {"ana": {"type": "seller", "name": "Ana"}, "ben": {"type": "seller", "name": "Ben"}},
  "actions": {
    "set_price": {"by": "seller", "description": "Set your price for this hour.",
      "params": {"price": {"type": "number", "min": 0.25, "max": 5}},
      "do": ["$actor.price = $params.price"], "terminal": true}
  },
  "stages": [{"name": "pricing", "turns": "simultaneous"}],
  "events": [{"phase": "end", "each": "seller",
    "do": ["$share = (1 / $it.price) / $sum(seller, 1 / $it.price)",
           "$it.earned += $round(40 * $share) * $it.price"]}],
  "views": {"market": {"for": "seller", "title": "Stands", "of": "seller",
    "show": "{name}: price {price|money}, earned {earned|money}"}},
  "metrics": {"avg_price": "$avg(seller, $it.price)"},
  "outputs": {"winner": {"expr": "$top(seller, $it.earned, 1)[0].name", "type": "text"},
              "avg_price": {"expr": "$metrics.avg_price", "type": "number"}}
}
```

Run it: `fg_env.run(contract, seed=1)` (random agents), `fg_env.run(contract, {"seller": my_llm})`.
"""

_MODEL = """\
## How a run works

Each round: scheduled effects → events (phase start) → physics step → each stage in order →
events (phase end) → metrics sampled → invariants and end conditions checked. A run ends when
an `end` condition holds, an effect `end`s it, or `clock.rounds` is used up.

A stage wakes agents (`who`, in `order`). An agent's turn is a short session: it reads its
brief + update, calls tools (legal actions, `look`, `inspect`, `end_turn`) until it ends the
turn, uses `max_actions`, or runs out of `max_calls`.
* `turns: sequential` — one agent at a time; actions apply immediately and the tool result is
  the actual outcome.
* `turns: simultaneous` — everyone sees the same state; actions are submitted, then committed in
  order after all have chosen (sealed bids, votes, simultaneous moves). Outcomes arrive as news.
* `until` repeats passes within the round (deliberation until everyone is ready).
* `quiet: skip` skips agents with nothing new since their last turn (from the second pass on;
  the first pass always wakes everyone).
* `look` and `inspect` calls count toward `max_calls`.
* A stage with `actions: []` wakes nobody: use it as a pure resolution step (`on_enter`/`on_exit`).
* `must_act: true` removes `end_turn` while an action is available; `on_idle` effects run for each agent
  that ends a turn without acting (`$actor`) — a forfeit or a default move.
* `terminal` may be an expression checked after the action applies (`"$world.jump_finished"`), so a
  move can end the turn only sometimes (multi-jumps).
* Physics steps at the start of every round, including round 1, before any stage.
* Invariants are checked after every action and effect block: write them for states that must hold
  at all times, not ones that only settle at the end of a stage.
* `end` conditions are checked after the start events, after each stage, and at the end of the round.
  To end at once in the middle of a stage (a winning move), use the `end` effect inside the action.

What an agent reads:
* brief (static, cacheable): name, situation, rules, its identity and role text.
* update: time label and stage, why it is acting, "Since your last turn" (announcements of
  others' actions, outcomes of its own simultaneous actions, record entries, event news), then
  every declared view that applies. Text written by participants is wrapped «like this».
* tools: one per legal action with a JSON Schema (entity choices as enums, numeric bounds when
  they depend only on the actor), plus look/inspect/end_turn. Invalid calls return what to fix.

Unless an action is `private` or sets `announce`, others read a default line
"Name: action (args)."; an action that posts to a record announces nothing extra (the entry
is the news). Text an agent types (text params) keeps its provenance wherever it is stored and
always renders «quoted», in news, views and outcomes.

An action applies atomically: if any effect `fail`s or a `transfer` lacks funds, every change
is rolled back and the agent is told why. Contract errors (bad expression at run time) stop
the run with status `failed` and the path of the broken rule.
"""

_EXPRESSIONS = """\
## Expressions

Any string containing `$name` is an expression; other strings are literal text.
* Roots: `$actor`, `$params`, `$it`, `$inputs`, `$world`, … (which ones depend on where — see below).
* Functions: `$count(buyer, $it.cash > 0)`. Per-item arguments bind `$it` (and `$i`).
* Bare words are text: `$actor.status == open`, `$count(offer)`. `true false null` are literals.
  Quote text with spaces: `$actor.mood == 'very happy'`.
* Operators: `+ - * / // % **`, `== != < <= > >=`, `and or not` (`&& || !`), `in`,
  `a if cond else b`, lists `[1, 2]`, indexing `$top(offer, $it.price, 1)[0]`.
* Entities expose `id name type alive at` and their props. Comparing an entity with an id works.
* Maps: `{wage: 3, 'job years': 2}`; read with `.key` or `$get(map, key, default)`.
* Nested per-item functions rebind `$it`; the enclosing item is `$outer`:
  `$sum(trader, $sum(order, $it.qty, $it.owner == $outer.id))`.
* Every function call needs its `$`: `$max(a, b)`, never `max(a, b)`.
* `a or b` gives the first truthy value (a default: `$x or 0`); `a and b` the first falsy one.
* A def without arguments reads like a value: `$negotiating` or `$negotiating()`.
* `$pending` lists what the agent already did or submitted this turn (`{action, ...args}`): use it in
  param `where` or `when` to stop ordering the same army twice.
* A param `where` may read earlier params: `{"to": {"type": "entity", "of": "province",
  "where": "$linked($params.army.at, $it.id, border)"}}` (the tool then lists every province and
  validation enforces the rule).
* Reserved roots cannot be used as local names: $actor $params $it $i $row $inputs $world $physics
  $clock $round $stage $metrics $series $arm $viewer $event $outer.
* Contract `defs` are called like built-ins: `$utility($actor, $params.offer)`.
* Bare words are text even when they match a property name: write `$actor.bet`, not `bet`.
* Strict: unknown props, missing roots and type errors are errors, never silent zeros.

Roots available by location (plus everywhere: $inputs $world $physics $clock $round $stage
$metrics $series $arm):
| where | extra roots |
|---|---|
| actions.*.when | $actor |
| params.*.where | $actor $it $i |
| params.*.min/max/values/default | $actor $params (earlier params) |
| actions.*.chance/do/otherwise/outcome/announce | $actor $params + locals |
| stages.*.who/order | $it $i |
| stages.*.brief | $actor |
| views.*.when/of | $actor |
| views.*.where/sort/show | $actor $it $i |
| records.*.visible | $viewer $it (entry) |
| records.*.show | $it (entry: author, round, fields) |
| events.*.where/do (with each) | $it $i |
| population.*.where/weight | $row |
| population.*.props/id/name | $row $i ($i counts from 1) |
| population.*.brief, entities.*.brief | $actor (+ $row $i for population) |
| types.*.inspect | $viewer $it |
| defs.*.expr | the def's args |
| blocks.*.do | the block's args + locals |
| policies.*.rules.* | $actor |
| outputs.* | $outputs (earlier outputs) |

`$clock` fields: round rounds left unit date label. `$metrics.x` = latest value; `$series.x` = list per round.
"""

_TEMPLATES = """\
## Templates (show, outcome, announce, say, brief, id, name)

`"[{id}] {name} · {price|money} · {rating|1}★"`
* `{field}` reads the template's subject (`$it` in list views and record show, `$actor` in
  single-line views and stage briefs). Elsewhere use full expressions: `{$params.qty}`.
* `{$expr}` any expression, including ones that start with a quote: `{$'yes' if $x else 'no'}`.
  `{value|format}` formats: FORMATS. In an expression use `$fmt(value, 'pct')`.
* A text field holding only an expression (`"say": "$inputs.headline"`) renders its value.
* Effect values (post fields, emit data, create props) that contain `{$…}` render as templates:
  `{"post": "log", "text": "{$actor.name} bid {$params.amount|money}"}`.
* Numbers print compactly; entities print as their name; lists join with commas; null is `—`.
* `{{` and `}}` are literal braces.
"""

_EFFECTS = """\
## Effects (actions.do/otherwise, events.do, stages.on_enter/on_exit)

Assignment text:
* `"$actor.cash -= $params.qty * $params.offer.price"` — also `=`, `+=`, `*=`, `/=`; targets are
  entity props (`$actor.x`, `$params.offer.x`, `$it.x`), `$world.x`, `$physics.x`.
* `"$total = $params.qty * 2"` — a local (`$total`) usable by later effects and the outcome.
* `+=`/`-=` on a list prop append/remove an item.
* Element assignment: `"$world.board[$i] = $actor.mark"`, `"$actor.scores[round_2] += 1"` (lists and maps).
* Numeric props are clamped to their min/max; types are enforced.

Operation objects (exactly one operation key each):
OPS
"""

_EFFECT_EXAMPLES = {
    "if": '{"if": "$cost > $actor.cash", "then": [...], "else": [...]}',
    "each": '{"each": "offer", "where": "$it.stock == 0", "do": ["$it.listed = false"]}  (with "as": "o", write $o instead of $it)',
    "create": '{"create": "review", "count": 1, "name": "Review {$i}", "props": {"stars": "$params.stars"}, "at": null, "as": "made"}',
    "remove": '{"remove": "$params.target"}',
    "transfer": '{"transfer": "cash", "from": "$actor", "to": "$params.seller", "amount": 10}  (fails the action if short)',
    "link": '{"link": "follows", "from": "$actor", "to": "$params.who", "value": 1}',
    "unlink": '{"unlink": "follows", "from": "$actor", "to": "$params.who"}',
    "move": '{"move": "$actor", "to": "$params.place"}',
    "post": '{"post": "chat", "text": "$params.text", "to": "$params.who"}  (record fields as keys; to = private recipients)',
    "emit": '{"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)", "data": {}}',
    "fail": '{"fail": "You cannot afford that."}  (roll back the action; text goes to the actor)',
    "end": '{"end": "bankrupt", "winner": "$top(player, $it.score, 1)[0]", "say": "..."}',
    "after": '{"after": 3, "do": [...]}  (runs 3 rounds later with the same locals)',
    "wake": '{"wake": "$params.who", "why": "{$actor.name} asked you a question."}',
    "repeat": '{"repeat": 1000, "while": "$count(order) > 1", "do": [...]}  (error if still true at the limit)',
    "block": '{"block": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}  (runs a named effect list from `blocks`)',
}

_PATTERNS = """\
## Patterns for common mechanics

* Money & trade: number props + `transfer` (atomic, never negative). Invariants like
  `"$all(trader, $it.cash >= 0)"` guard the books.
* Markets / order books: orders as entities (`create` with side, price, qty, owner); an end-phase
  event matches with `repeat` while best bid ≥ best ask using `$top`/`$bottom`; trades update
  holdings and `remove` filled orders.
* Voting / ballots: a simultaneous stage with a `vote` action writing `$actor.vote`; an
  `on_exit` effect tallies with `$mode($map(voter, $it.vote))` or `$count(voter, $it.vote == yes)`.
* Deliberation: records (`chat`) + a sequential stage with `until: "$all(member, $it.ready)"`
  and `quiet: skip`; a `say` action posts and clears readiness.
* Hidden roles: a world prop `deck` defaulting to `$shuffle([wolf, wolf, seer, villager, ...])`, then
  `population` props `{"role": "$world.deck[$i - 1]"}` and a private per-entity
  `brief: "You are a {$actor.role}."`; gate actions with `when: "$actor.role == wolf"`.
* Hidden information: `private` props (hidden from others' inspect), per-type views, record
  `visible` rules, `to` on posts/emits, `private: true` actions (no announcement).
* Board and card games: `space.grid` + piece entities with `at`, or cell entities; legal moves
  via entity params with `where`; decks as card entities with an `order` prop and `$shuffle`;
  win checks in `end`.
* Populations from data: `inputs` of type table + `population.from/where/weight/count` +
  per-row props; traits via `$normal`, `$beta`, `$choice`.
* Networks: `relations` + `links` generators (`small_world`, `random`, `ring`, `complete`). On a
  one-way relation `random` draws each direction on its own, and `p` may depend on the pair
  (`"0.1 if $to.influencer else 0.02"`) for influencers and homophily;
  `$neighbors(entity, kind)` in views, effects, contagion events.
* Continuous dynamics: `physics` vars with rates (math over bare names), `read` from the world,
  `write` back to props; effects adjust `$physics.x` (policy shocks).
* Scenarios & experiments: `inputs` for scenario knobs, `arms` for variants (input overrides or
  patches), `events` with `at`/`every`/`chance`/`arms` for shocks; `fg_env.experiment` runs arms
  with shared seeds.
* Families of agents: `types.trader` with shared props, then `types.market_maker: {"extends": "trader"}`;
  `$count(trader)`, `by: trader`, views `for: trader` and `brief.roles.trader` cover every kind.
* Reusable logic: `defs` for formulas (`"utility": {"args": ["side", "offer"], "expr": "..."}`) and
  `blocks` for effect lists (`{"block": "match", "with": {"order": "$made"}}`).
* Scoping inspection: `types.X.inspect: false` (or an expression over `$viewer` and `$it`) hides
  entities from the inspect tool; `private` props hide single values.
* Boards and tables in views: `"bullet": false` prints lines without "- ". View titles are templates.
* Participants keyed by a parent type (`{"tier": ...}`) and `policy` on a parent type reach every subtype.
* Calendars: `clock.start` with unit day, week, month, year, hour or minute adds the date to the time label.
* Policy rules with `each` act once per item: `{"each": "$filter(army, $it.owner == $actor.id)",
  "do": "hold", "with": {"army": "$it"}}`.
* Coded participants: `policies` rules (first legal matching rule wins) for crowds and baselines;
  set `types.X.policy` to make them the default.
"""

_RUNNING = """\
## Running (Python)

```python
import fg_env
env = fg_env.load("shop.json", inputs={"budget": 50}, seed=7, arm=None)
print(env.preview("shopper_1"))                   # brief, update, tools, token estimates
result = env.run({"shopper": "policy:thrifty", "owner": my_agent})
result.outputs, result.metrics, result.series, result.stats, result.events, result.summary()
snap = env.snapshot(); env2 = fg_env.Env.restore("shop.json", snap)   # between rounds; JSON-safe
exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"]); print(exp.table())
exp.deltas("control")   # paired promo − control per output: mean, sd, ci95, clear (CI excludes 0)
```

A participant is any callable taking a `Wake`:
```python
def my_agent(wake):
    wake.brief, wake.update, wake.tools            # read
    result = wake.call("buy", {"offer": "latte", "qty": 1})   # result.ok, result.text, result.ended
    wake.end()
```
`Wake`: `entity_id name type round stage reason me` (own props), `brief`, `update`, `tools` (each a
`ToolSpec`: name, description, input_schema, kind act|look|end, terminal), `tools_for("anthropic"|"openai")`,
`call(name, args)` → `ToolResult(ok, text, ended, data)` (`data.error` is `invalid` or `rejected`),
`end()`, `done`, `calls_left`, `actions_left`. In a simultaneous stage a choice is tried at submit, so
a choice that could not happen is refused immediately and does not use up the turn.
`env.step(participants)` runs one round; `env.run(participants, rounds=N)` runs N more (an unfinished
run returns provisional outputs). `env.run(..., stop=lambda env: ...)` is checked before every round,
stage, pass and sequential turn; the next `run` continues exactly where it stopped (finishing that
round counts as one of `rounds`). Snapshots are taken between rounds. A participant that raises fails
the run with its entity id; experiments keep such runs as `status="failed"` and carry on. Read state with `env.entity(id)`, `env.entities(type)`, `env.props`,
`env.result()`, `env.finished`. `env.preview(id)` plays the start of the next round on a copy and shows
exactly the turn the agent will get.

LLM participants: `fg_env.participants.anthropic(anthropic.Anthropic(), "claude-sonnet-5")` or
`fg_env.participants.openai(client, model)`; they cache the brief and loop over tool calls, retry rate
limits, timeouts and server errors (`retries=4`), then fail the run or, with `on_error="end_turn"`,
forfeit the turn. Their real token usage is in `result.stats` (`llm_calls`, `input_tokens`,
`output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `llm_retries`, `forfeits`); your own
participants can add theirs with `wake.record_usage(...)`.
Built-ins: `"random"`, `"idle"`, `"policy:<name>"`.

`result.events` is the ordered log: `{seq, round, kind, text, actor, to, stage, data}` where kind is
`action` (data: action, params, success), `outcome` (a sealed action's result, to its actor),
`record` (data: record, entry, fields), `news` (event `say`), any `emit` name, or `end` (data: ended_by, winner).
`result.winner` is set by `end` conditions or effects that give `winner`.

CLI: `fg-env check file.json` (static check plus one played round; `--rounds 0` for static only),
`fg-env preview file.json agent_id --rounds 5 --agent trader=policy:quote` (see a mid-run turn),
`fg-env check|run|preview|experiment|guide|schema` (`fg-env run file.json --seed 1
--input budget=50 --agent shopper=policy:thrifty --json`).
"""

_CHECKLIST = """\
## Quality checklist (what makes an environment great for LLM agents)

* Brief: situation and rules in a few plain sentences; the role text says what this agent wants.
* Views: only what matters for the decision, ranked (`sort`, `desc`) and capped (`limit`),
  names first with `[id]` handles, numbers with units (`|money`, `|pct`). Put rarely needed
  detail behind `look: true`.
* Tools: clear descriptions; tight params (`where`, `min`, `max`, `values`); `fail` messages that
  say what to do instead; `terminal: true` for the one decisive action of a turn.
* Stages: `simultaneous` for sealed choices; `max_actions` sized to the decision; `quiet: skip`
  in long deliberations.
* Measurement: metrics for the dynamics you care about; typed outputs for every result a caller
  needs; invariants for conservation laws.
* Always: run `check` until clean, `preview` every agent type, run a few seeds, read
  `result.stats` (invalid_rate and avg_update_tokens should stay low).
"""

_SECTIONS: List[Tuple[str, List[Type[BaseModel]]]] = [
    ("inputs", [C.InputSpec]), ("brief", [C.Brief]), ("clock", [C.Clock]),
    ("space", [C.Space, C.GridSpace, C.GraphSpace, C.PlaneSpace]), ("world", [C.PropSpec]),
    ("types", [C.TypeSpec, C.PropSpec]), ("entities", [C.EntitySpec]), ("population", [C.PopulationSpec]),
    ("relations", [C.RelationSpec]), ("links", [C.LinkSpec]), ("physics", [C.PhysicsSpec, C.PhysicsVar]),
    ("records", [C.RecordSpec]), ("actions", [C.ActionSpec, C.ParamSpec, C.Condition]),
    ("stages", [C.StageSpec]), ("views", [C.ViewSpec]), ("events", [C.EventSpec]),
    ("policies", [C.PolicySpec, C.PolicyRule]), ("metrics", [C.MetricSpec]), ("outputs", [C.OutputSpec]),
    ("end", [C.EndSpec]), ("arms", [C.ArmSpec]), ("invariants", [C.InvariantSpec]),
    ("defs", [C.DefSpec]), ("blocks", [C.BlockSpec]), ("mechanisms", []),
]

_SHAPES = {
    "inputs": "{name: InputSpec}", "brief": "Brief", "clock": "Clock", "space": "Space", "world": "{prop: PropSpec}",
    "types": "{type: TypeSpec}", "entities": "{id: EntitySpec}", "population": "[PopulationSpec]",
    "relations": "{relation: RelationSpec}", "links": "[LinkSpec]", "physics": "PhysicsSpec",
    "records": "{record: RecordSpec}", "actions": "{action: ActionSpec}", "stages": "[StageSpec]",
    "views": "{view: ViewSpec}", "events": "[EventSpec]", "policies": "{policy: PolicySpec}",
    "metrics": "{metric: MetricSpec | expr}", "outputs": "{output: OutputSpec | expr}", "end": "[EndSpec]",
    "arms": "{arm: ArmSpec}", "invariants": "[InvariantSpec | expr]",
    "defs": "{name: DefSpec | expr}", "blocks": "{name: BlockSpec}",
    "mechanisms": "{name: {kind, ...config}} — native building blocks; see the mechanisms part",
}


def _type_name(annotation: Any, field: str) -> str:
    if field in ("do", "otherwise", "on_enter", "on_exit"):
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


def _reference() -> str:
    out = ["## Contract reference", "",
           "Top level: `fg_env` (\"1\"), `name` (required), `description`, and the sections below. "
           "Unknown fields are errors."]
    for section, models in _SECTIONS:
        out += ["", f"### {section}: {_SHAPES[section]}"]
        out += [_fields(model) for model in models]
    return "\n".join(out)


def _functions() -> str:
    lines = ["## Functions", ""]
    for spec in sorted(FUNCTIONS.values(), key=lambda s: s.name):
        lines.append(f"- `${spec.signature}` — {spec.doc}")
    return "\n".join(lines)


def _effects() -> str:
    ops = "\n".join([f"- `{op}`: {_EFFECT_EXAMPLES[op]}" for op in EFFECT_OPS]
                    + [f"- `{name}`: {spec.example}" for name, spec in OPS.items()])
    return _EFFECTS.replace("OPS", ops)


def _mechanisms() -> str:
    lines = ["## Mechanisms (native building blocks)", "",
             "Declare `\"mechanisms\": {name: {\"kind\": ..., ...config}}`. Each expands into ordinary actions,",
             "stages, world props and events you can read, preview and override (declare the same name",
             "yourself to replace a generated part). Kinds:"]
    for kind, spec in sorted(MECHANISMS.items()):
        lines += ["", f"### `{kind}`", spec.doc, "", "Config:"]
        for field_name, info in spec.config.model_fields.items():
            default = "required" if info.is_required() else f"default {json.dumps(info.default, default=str)}"
            lines.append(f"- `{field_name}` ({default}): {info.description or ''}")
        if spec.example:
            lines += ["", "```json", json.dumps({"mechanisms": {"my_" + kind: spec.example}}, ensure_ascii=False), "```"]
    return "\n".join(lines)


GUIDE_PARTS: Dict[str, Any] = {
    "overview": lambda: _OVERVIEW,
    "model": lambda: _MODEL,
    "reference": _reference,
    "expressions": lambda: _EXPRESSIONS,
    "functions": _functions,
    "templates": lambda: _TEMPLATES.replace("FORMATS", ", ".join(f"`{f}`" for f in FORMATS)),
    "effects": _effects,
    "patterns": lambda: _PATTERNS,
    "mechanisms": _mechanisms,
    "running": lambda: _RUNNING,
    "checklist": lambda: _CHECKLIST,
}


def guide(part: Optional[str] = None) -> str:
    """The authoring guide (all parts), or one part: overview, model, reference, expressions,
    functions, templates, effects, patterns, running, checklist."""
    if part is None:
        return "\n\n".join(render() for render in GUIDE_PARTS.values())
    if part not in GUIDE_PARTS:
        raise KeyError(f"unknown guide part '{part}' (parts: {', '.join(GUIDE_PARTS)})")
    return GUIDE_PARTS[part]()
