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
from .macros import MAX_MACRO_DEPTH, MAX_MACRO_ITEMS
from .registry import FAMILIES, OPS, FamilySpec, ModeSpec
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

Each round: scheduled effects → feeds → events (phase start) → physics step → each stage in order →
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
* `time_limit` gives each agent wall-clock seconds for its turn (a number, or an expression over `$actor`;
  `env.run(..., time_limit=30)` covers stages that set none). Past it the turn ends, later calls are
  refused, a `timeout` event is logged and `on_timeout` runs instead of `on_idle`. The agent's update says
  how long it has. A participant that finishes in time plays exactly as it would without a limit.
* `atomic: true` makes a turn's actions apply together: each applies at once (the agent sees its result),
  but triggers, reactions and invariants wait until the turn ends. `valid` conditions (`$actor`, `$pending`)
  are checked when a turn that acted ends; if one fails, every action of the turn is undone, the agent is
  told `why` and plays the turn again (castling through check, a full backgammon move). `valid` makes a
  stage atomic. In a simultaneous stage each agent's choices commit or are undone together.
* Views with `"for": "spectator"` are an omniscient picture for UIs and reports: rendered at the end of
  every round into `result.frames` (the last marked `final`) and on demand by `env.spectate()`, never
  shown to an agent. They have no `$actor`; randomness they draw never changes the run.
* Physics steps at the start of every round, including round 1, before any stage: world variables
  first, then `physics.per` dynamics for every entity, which read the world variables' new values.
* Lifecycle hooks: `types.X.on_create` / `on_remove` run for every entity of X (and its subtypes; an
  ancestor's hooks first) the moment it is created or removed — by an effect, a mechanism or a hook —
  inside that change, so a `fail` in a hook refuses it. Entities made at build run on_create once the
  whole world exists, in creation order (`on_create_at_build: false` skips them). `$it` is the entity;
  in on_remove it is already no longer alive. Hooks setting off hooks stop at 16 levels.
* Invariants are checked after every action and effect block: write them for states that must hold
  at all times, not ones that only settle at the end of a stage. `"check": "round"` checks one only at
  the end of every round (a conservation sum over a big crowd then costs one pass a round, not one per
  change); `"check": "end"` once, when the run finishes.
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
| stages.*.brief, stages.*.time_limit, stages.*.on_timeout | $actor |
| stages.*.valid (expr and why) | $actor $pending |
| views.*.when/of | $actor |
| views.*.where/sort/show | $actor $it $i |
| views with for: spectator | no $actor ($it $i in lists) |
| records.*.visible | $viewer $it (entry) |
| records.*.show | $it (entry: author, round, fields) |
| events.*.where/do (with each) | $it $i |
| population.*.where/weight | $row |
| population.*.props/id/name | $row $i ($i counts from 1) |
| population.*.brief, entities.*.brief | $actor (+ $row $i for population) |
| types.*.inspect | $viewer $it |
| types.*.on_create/on_remove | $it (the entity) + locals |
| relations.*.props.*.default | $from $to |
| links.*.props | $from $to (+ $row with `rows`) |
| physics.per.*.read/where | $it |
| feeds.*.query/when/fallback | — |
| defs.*.expr | the def's args |
| blocks.*.do | the block's args + locals |
| policies.*.rules.* | $actor |
| outputs.* | $outputs (earlier outputs) |

`$clock` fields: round rounds left unit date label. `$metrics.x` = latest value; `$series.x` = list per round.
"""

_MACROS = """\
## Macros (repeat structure from data)

An object with `for` and `make` is a macro: it repeats `make` once per value, replacing `{name}`
placeholders (the `as` name) in strings and keys. Expanded when the contract is read, before
mechanisms, in every file on its own (see the result with `fg_env.expand(contract)` or `fg-env expand file.json`).

```json
"stages": [{"for": ["flop", "turn", "river"], "as": "street",
            "make": {"name": "{street}", "actions": ["bet_{street}"]}}],
"actions": {"bet_{street}": {"for": ["flop", "turn", "river"], "as": "street",
            "make": {"by": "player", "do": ["$actor.bets = $actor.bets + ['{street}']"]}}}
```

* In a list a macro becomes one item per value. As a map entry whose key holds one of its
  placeholders (`"bet_{street}"`) it becomes one entry per value; under any other key
  (`"stages": {"for": ...}`) it becomes the list of made values.
* `for`: a list (of values or objects), `{"range": n}` (0..n-1), `{"range": [start, end]}` or
  `{"range": [start, end, step]}` (the end excluded, like `$range`), or a placeholder giving a list
  from an outer loop (`"for": "{street.cards}"`).
* `{x}` alone in a string keeps the value's type (`"count": "{n}"` is a number); in longer text it is
  written out (lists and maps as JSON). `{x.field}` reads a field, `{x.0}` a list item, `{n+1}` and
  `{n-1}` offset a whole number. `"index": "i"` names the 0-based position.
* Nest loops by making a macro whose `make` is a macro: `"move_{a}_{b}"` with `for` a, `make` {`for` b …}.
* Only loop variables are replaced: template fields like `{name}` and `{{` stay as they are, so do
  not name a loop variable after a property you read in a template.
* Limits: MAX_ITEMS generated values per file, loops MAX_DEPTH deep. Two generated entries with one
  name, a missing field or a wrong `for` are errors with the macro's path.
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
* Links: `"$link($actor, $params.who, trusts).value += 0.1"`, `"$link($actor, $params.who, trusts).since = $round"`
  (the link must exist; its value is clamped to the relation's min/max, fields are typed like props).
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
    "link": '{"link": "trusts", "from": "$actor", "to": "$params.who", "value": 0.8, "props": {"since": "$round"}}  (creates or updates: without `value` an existing link keeps its value and a new one gets the relation\'s `default`; `props` sets link fields, a new link starting from their defaults)',
    "unlink": '{"unlink": "follows", "from": "$actor", "to": "$params.who"}',
    "move": '{"move": "$actor", "to": "$params.place"}',
    "post": '{"post": "chat", "text": "$params.text", "to": "$params.who", "delay": 2, "drop": 0.1}  (record fields as keys; to = private recipients; optional `delay` — rounds, or time on a continuous clock — and `drop` chance)',
    "emit": '{"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)", "data": {}, "delay": 1}  (optional `delay` and `drop`, as for post)',
    "fail": '{"fail": "You cannot afford that."}  (roll back the action; text goes to the actor)',
    "end": '{"end": "bankrupt", "winner": "$top(player, $it.score, 1)[0]", "say": "..."}',
    "after": '{"after": 3, "do": [...]}  (runs 3 rounds later with the same locals; on a continuous clock, 3 time units later)',
    "wake": '{"wake": "$params.who", "why": "{$actor.name} asked you a question."}  (a turn later; "now": true — they react right away, before this turn continues; "in": 5 — continuous clock, that much later; "drop": 0.2 — the wake may be lost)',
    "repeat": '{"repeat": 1000, "while": "$count(order) > 1", "do": [...]}  (error if still true at the limit)',
    "block": '{"block": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}  (runs a named effect list from `blocks`)',
    "chance": '{"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails", "do": [...]}], '
              '"as": "coin"} or {"chance": "deal", "outcomes": "$world.deck", "weight": "1", "as": "card", "do": [...]}  '
              '(picks one outcome from the listed distribution, logged as a `chance` event; `fg_env.game` can '
              'enumerate and choose outcomes instead of sampling them)',
}

_PATTERNS = """\
## Patterns for common mechanics

* Data files: `"inputs": {"households": {"type": "table", "source": "households.csv", "columns": {"income":
  "number", "size": "int"}}}` then `"population": [{"type": "person", "from": "$inputs.households"}]`. Files are
  read from the contract's folder (or `data_dir=`); undeclared CSV columns stay text. Also `.json` and `.jsonl`.
* Continuous time (clinics, queues, trading days, emergencies): `"clock": {"mode": "continuous",
  "unit": "minute", "horizon": 480}`, a stage with `"turns": "scheduled"`, and `"duration"` on actions.
  Each agent acts when its time comes (earliest first) and next acts `duration` later (or the stage
  `interval` if it did nothing timed); `$clock.time` is the time; `after` and `wake` with `in` schedule
  by time; physics rates are per time unit. The run jumps from one due moment to the next.

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
* Spaces (agent-based models): `"space": {"grid": {"rows": "$inputs.size", "cols": "$inputs.size",
  "neighborhood": "moore", "torus": true}, "capacity": 1}` — `von_neumann` (4 neighbours), `moore` (8) or `hex`
  (6, axial [r, q]); sizes may read `$inputs` so they can be swept. Entities with `at` are indexed: `$at(pos, type?)`,
  `$near($it, radius, type?)`, `$nearest($it, type, where?)`, `$cells($it, radius?)`, `$empty(type?)`,
  `$random_empty(type?)` read a few cells, never every entity. `move` and `create` into a full cell are refused
  like a `fail`; on a torus positions wrap (`[$it.at[0] + 1, $it.at[1]]` steps off the edge and back on).
  Graphs measure path length; planes (`$near`, `$nearest`) straight lines.
* Values on cells (sugar, pheromone, fire): `"space": {..., "layers": {"sugar": {"type": "int", "default":
  "$peak($cell)", "max": 4}}}`; read `$layer(sugar, $it)`; change with `{"layer": "sugar", "at": "$it", "set": 0}`,
  a whole layer with `{"layer": "sugar", "set": "$min($value + 1, 4)"}` (every cell reads the old values),
  `{"layer": "scent", "diffuse": 0.1}` and `{"layer": "scent", "decay": 0.05}`. Layers are kept in snapshots.
* Cellular automata and simultaneous updates: an `each` event with `"sync": true` — every item's rules read the
  world as it was before the event and all writes land together (Game of Life is one event:
  `"$n = $count($near($it, 1), $it.on)", "$it.on = $n == 3 or ($it.on and $n == 2)"`). `"order": "random"` (or an
  expression, lowest first) orders the items of any `each` event.
* Board and card games: `space.grid` + piece entities with `at`, or cell entities; legal moves
  via entity params with `where`; decks as card entities with an `order` prop and `$shuffle`;
  win checks in `end`.
* Populations from data: `inputs` of type table + `population.from/where/weight/count` +
  per-row props; traits via `$normal`, `$beta`, `$choice`. Correlated traits: draw them together in one list
  prop, then read the parts (props read earlier props through `$it`): `"z": "$mvnormal([170, 70], [[100, 64],
  [64, 64]])", "height": "$it.z[0]", "weight": "$it.z[1]"`.
* Networks: `relations` + `links` generators (`small_world`, `random`, `ring`, `complete`). On a
  one-way relation `random` draws each direction on its own, and `p` may depend on the pair
  (`"0.1 if $to.influencer else 0.02"`) for influencers and homophily;
  `$neighbors(entity, kind)` in views, effects, contagion events.
* Links with data: `"relations": {"trusts": {"props": {"since": {"type": "int", "default": "$round"},
  "channel": {"type": "enum", "values": ["work", "family"], "default": "work"}}}}`. Read `$link(a, b, trusts).since`;
  list `$links($actor, trusts)` in views (`"show": "{target.name} via {channel}"`); set fields with `link` +
  `props`, by assignment, or in `links` (`props` over `$from`/`$to`; `rows` columns named like a field fill it).
* Continuous dynamics: `physics` vars with rates (math over bare names), `read` from the world,
  `write` back to props; effects adjust `$physics.x` (policy shocks). `noise` adds a random term
  (`"noise": "sigma*price"`, Euler–Maruyama, drawn from the run's seed).
* Per-entity dynamics (viral load, firm capital, habit strength): `"physics": {"per": {"person": {"vars":
  {"viral_load": {"rate": "growth*viral_load - immunity*viral_load", "noise": "0.2*viral_load"}},
  "read": {"exposure": "$count($neighbors($it, contact), $it.sick)"}, "write": {"sick": "viral_load > 5"}}}}`.
  Every person integrates its own number props; rates read its number props, the type's `params` and
  `read`s (per entity, over `$it`) and world physics names. `where` limits who integrates this step.
  Entities couple through `read` (explicit in time): the values are fixed for the whole step.
* Latency and lossy channels: `"delay": 2` on `post`/`emit` delivers the message 2 rounds (or clock units)
  later with its content as it was when sent; `"drop": 0.1` loses it (also on `wake`), rolled from the
  run's seed when sent. A refused action sends nothing. Entries carry the round they arrive.
* External data (prices, news, weather): `"feeds": {"oil": {"host": "market", "into": "world.oil_price",
  "query": {"symbol": "BRENT", "date": "{$clock.date}"}, "fallback": "$world.oil_price * $uniform(0.98, 1.02)"}}`,
  or `"into": "records.news"` for entries. Bind the host when loading: `fg_env.load(path, hosts={"market":
  adapter})`, where the adapter is any object with `fetch(request)`; `fg_env.sdk.host.adapters.historical(rows,
  at="date", value="close")` replays a price history for backtests. Answers are recorded on the host tape:
  snapshots, restores and replays never ask again, and host text reaches agents «quoted».
* Scenarios & experiments: `inputs` for scenario knobs, `arms` for variants (input overrides or
  patches), `events` with `at`/`every`/`chance`/`arms` for shocks; `fg_env.experiment` runs arms
  with shared seeds (`branch_at=N`: every arm continues from one shared history of N rounds).
* Games: a `game` section names the seats and what each scores (`"game": {"players": "player", "seat":
  "$it.seat", "returns": "$actor.chips - 10", "utility": "zero_sum"}`); dealt cards and dice as `chance`
  effects (`{"chance": "deal", "outcomes": "$world.deck", "as": "card", "do": [...]}`) so solvers can
  enumerate them; `must_act` stages so a seat cannot stall; `step` on number params so bids have ids.
* Families of agents: `types.trader` with shared props, then `types.market_maker: {"extends": "trader"}`;
  `$count(trader)`, `by: trader`, views `for: trader` and `brief.roles.trader` cover every kind.
* Bookkeeping on birth and death: `"types": {"firm": {"on_create": ["$world.firms_founded += 1",
  {"link": "supplies", "from": "$it", "to": "$top(supplier, $it.capacity, 1)[0]"}], "on_remove":
  [{"each": "$filter(job, $it.employer == $outer.id)", "do": [{"remove": "$it"}]}]}}` — every firm, however it
  was created, is counted and connected; closing one lays off its jobs.
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
fg_env.sweep("shop.json", {"price": {"low": 1, "high": 5, "steps": 5}}, runs=10).table()   # also sensitivity, calibrate, backtest
fg_env.behavior_checks("shop.json")   # constant outputs, inputs that change nothing, actions and stages never used
fg_env.tournament("duel.json", {"greedy": "policy:greedy", "llm": my_agent}, games=20).summary()
# seats rotate and share seeds; Elo with intervals, Glicko-2, Nash average, α-Rank, votes, cost per entrant
d = fg_env.describe("duel.json"); d.markdown, d.metadata   # ODD description; turns, chance, information, players, length

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
Async participants: an `async def` (or an object with an async `__call__`, or a function that returns an
awaitable) works everywhere, and a simultaneous stage runs them concurrently with the same deterministic
result. Inside an event loop use `result = await env.arun(participants, ...)`: participants run on that loop,
so clients bound to it work. `wake.time_limit` and `wake.time_left` give the turn's deadline.
`fg_env.load(..., exposures=True)` records what every agent was shown on every wake in `result.exposures`,
`{"texts": {hash: text}, "wakes": [...]}`: brief, update and view hashes and sizes, news event sequence
numbers, tools offered, every call with its arguments and result, timeouts and undone turns — every text
stored once. `$seen(agent, item)` asks whether an agent was shown an event, a record entry or a view by name;
a contract that uses it records exposures automatically. `result.frames` and `env.spectate()` give the
spectator views.

Traces: a run with `exposures=True` is a trace (`fg-env run file.json --trace run.jsonl`); `result.save("run.json")`
or `.jsonl`, `fg_env.RunResult.load(path)`. `t = fg_env.trace(result_or_file)`: `t.overview()` (per agent: turns,
calls, invalid rate, timeouts, tokens), `t.turn(7)` or `t.turn("ana", 3)` (what the agent read, the tools offered,
every call with its result), `t.timeline("ana")`, `t.search("bribe")` (in what agents read or wrote), `t.invalid()`
(refused calls with the correction given), `t.agent("ana")`; each has `.data` and prints as text
(`fg-env trace run.jsonl turn ana 3`). `t.replay("shop.json")` runs the contract again offline with the recorded
calls (`fg_env.participants.replay(t)`) and host answers, and reports the first divergence — a turn, the brief or
update text, the tools offered, a call result, an event or the ending; `fallback="policy:x"` plays on after it
(`fg-env trace run.jsonl replay shop.json`, exit 1 on a divergence). Each wake's `steps` are what it replays.
Evaluation: `fg_env.evaluate(suite, focal=my_agent, background="policy:reciprocate", seats="villager",
score="$outputs.cash[$seat]", modes={"resident": 0.75, "visitor": 0.25}, runs=20).summary()` runs every scenario and
mode with `focal` in a seeded draw of the seats and again with `baseline` (default: the background) in the same seats
on the same seed: focal score per focal seat, the baseline's, the paired difference with a 95% interval and cost, per
scenario, mode, tag, held out vs in sample and overall. A suite is a contract, a list of scenarios
`{contract, name, inputs, arm, seats, score, background, baseline, modes, tags, held_out}`, or a
`{"scenarios": [...]}` file (`fg-env evaluate suite.json --focal policy:x --mode visitor=0.25`).
Budgets: `env.run(..., budget={"tokens": 200000, "calls": 500, "host_calls": 50, "seconds": 600,
"on_exhaust": "end"})` caps a run: reported input + output tokens, tool calls, host answers on the tape, wall-clock
seconds. It is checked before every round, stage, pass and turn (a turn in progress finishes): `end` ends the run
(`ended_by: "budget"`), `idle` lets it finish with every agent idle. `result.budget` has the limits, use and the
limit that ran out; snapshots keep it; `evaluate` and `fg-env run --budget tokens=200000` take one.
`env.step(participants)` runs one round; `env.run(participants, rounds=N)` runs N more (an unfinished
run returns provisional outputs). `env.run(..., stop=lambda env: ...)` is checked before every round,
stage, pass and sequential turn; the next `run` continues exactly where it stopped (finishing that
round counts as one of `rounds`). Snapshots are taken between rounds. A participant that raises fails
the run with its entity id; experiments keep such runs as `status="failed"` and carry on. Read state with `env.entity(id)`, `env.entities(type)`, `env.props`,
`env.result()`, `env.finished`. `env.preview(id)` plays the start of the next round on a copy and shows
exactly the turn the agent will get.

Copies, forks, games and gyms:
```python
with wake.clone() as branch:          # inside a turn: a private copy paused right here (fresh luck; same_luck=True)
    branch.call("buy", {"offer": "latte", "qty": 2}); outcome = branch.run("random")   # the real run never changes
twin = env.clone()                    # between rounds or stopped mid-round: continues exactly like env
what_if = env.fork(arm="promo", patch={...}, effects=["$world.tax = 0.2"])   # between rounds; refuses what cannot follow
game = fg_env.game("kuhn_poker.json"); state = game.new_initial_state()    # OpenSpiel-style
state.current_player(), state.legal_actions(), state.chance_outcomes(), state.child(action), state.returns()
state.information_state(seat), state.observation(seat, "struct"), state.apply_actions({0: a, 1: b})
env = fg_env.gym("nim.json", "a", others="random"); obs, info = env.reset(seed=1)
obs, reward, terminated, truncated, info = env.step({"tool": "take", "args": {"count": 2}})
```
A copy is rebuilt from the run's base and replays what its participants did, so it is exact (state, random
streams, turn numbers, log, recorded host answers) and costs a restore plus the round so far; turn time
limits never run out in a copy. It holds the whole world, hidden state included. Game action ids are fixed
when the game is created (one per combination of listed argument values; free text and lists are
parametric: apply them as `{"tool", "args"}`). `fg_env.load(..., chance=callable)` chooses chance outcomes.

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
`fg-env bench [files] --rounds 20` (ms per round, rounds per second and time per phase; no files: the
reference agent-based models), `fg-env check|run|preview|experiment|tournament|evaluate|trace|guide|schema` (`fg-env run file.json --seed 1
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
    ("imports", []), ("inputs", [C.InputSpec]), ("brief", [C.Brief]), ("clock", [C.Clock]),
    ("space", [C.Space, C.GridSpace, C.GraphSpace, C.PlaneSpace]), ("world", [C.PropSpec]),
    ("types", [C.TypeSpec, C.PropSpec]), ("entities", [C.EntitySpec]), ("population", [C.PopulationSpec]),
    ("relations", [C.RelationSpec]), ("links", [C.LinkSpec]), ("physics", [C.PhysicsSpec, C.PhysicsVar, C.EntityDynamics, C.EntityVar]), ("feeds", [C.FeedSpec]),
    ("records", [C.RecordSpec]), ("actions", [C.ActionSpec, C.ParamSpec, C.Condition]),
    ("stages", [C.StageSpec]), ("views", [C.ViewSpec]), ("events", [C.EventSpec]), ("triggers", [C.TriggerSpec]),
    ("policies", [C.PolicySpec, C.PolicyRule]), ("metrics", [C.MetricSpec]), ("outputs", [C.OutputSpec]),
    ("end", [C.EndSpec]), ("arms", [C.ArmSpec]), ("game", [C.GameSpec]), ("invariants", [C.InvariantSpec]),
    ("defs", [C.DefSpec]), ("blocks", [C.BlockSpec]), ("mechanisms", []),
]

_SHAPES = {
    "imports": "[path] — contract files merged into this one (relative to it, inside its folder); this contract's own entries win, and imported files may import others",
    "inputs": "{name: InputSpec}", "brief": "Brief", "clock": "Clock", "space": "Space", "world": "{prop: PropSpec}",
    "types": "{type: TypeSpec}", "entities": "{id: EntitySpec}", "population": "[PopulationSpec]",
    "relations": "{relation: RelationSpec}", "links": "[LinkSpec]", "physics": "PhysicsSpec", "feeds": "{feed: FeedSpec}",
    "records": "{record: RecordSpec}", "actions": "{action: ActionSpec}", "stages": "[StageSpec]",
    "views": "{view: ViewSpec}", "events": "[EventSpec]", "triggers": "[TriggerSpec]", "policies": "{policy: PolicySpec}",
    "metrics": "{metric: MetricSpec | expr}", "outputs": "{output: OutputSpec | expr}", "end": "[EndSpec]",
    "arms": "{arm: ArmSpec}", "game": "GameSpec", "invariants": "[InvariantSpec | expr]",
    "defs": "{name: DefSpec | expr}", "blocks": "{name: BlockSpec}",
    "mechanisms": "{name: {kind, mode, ...config}} — native building blocks by family; see the mechanisms part",
}


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
    lines = [f"- `{op}`: {_EFFECT_EXAMPLES[op]}" for op in EFFECT_OPS]
    lines += [f"- `{name}`: {spec.example}" for name, spec in OPS.items() if spec.select is None]
    for name, family in FAMILIES.items():
        if family.actions and any(family.actions.values()):
            lines.append(f"- `{name}`: {{\"{name}\": \"<{name} mechanism>\", \"action\": ...}} — actions: "
                         + "; ".join(f"{mode} {' '.join(_public(family, mode)) or '—'}" for mode in family.modes)
                         + f" (guide(\"{name}\"))")
    return _EFFECTS.replace("OPS", "\n".join(lines))


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


def _public(family: FamilySpec, mode: str) -> List[str]:
    """The actions of a mode an author writes (the mechanism's own bookkeeping actions left out)."""
    return [action for action, op in family.actions.get(mode, {}).items() if not op.internal]


def _mechanisms() -> str:
    from .mechanisms.families import SHARED

    lines = ["## Mechanisms (native building blocks)", "",
             "Declare `\"mechanisms\": {name: {\"kind\": <family>, \"mode\": <mode>, ...config}}`. Each expands into",
             "ordinary actions, stages, world props and events you can read, preview and override (declare the same",
             "name yourself to replace a generated part). A family has one effect op:",
             "`{\"<family>\": \"<mechanism name>\", \"action\": \"<action>\", ...}`. Read a family with",
             "`guide(\"<family>\")` and one mode with `guide(\"<family>.<mode>\")`.", "",
             "| kind | modes | for |", "|---|---|---|"]
    lines += [f"| `{name}` | {', '.join(family.modes) or '—'} | {family.doc} |" for name, family in FAMILIES.items()]
    lines += ["", "Every family names these the same way:"]
    lines += [f"- `{key}`: {meaning}" for key, meaning in SHARED.items()]
    return "\n".join(lines)


def _family_page(name: str) -> str:
    family = FAMILIES[name]
    lines = [f"## Mechanism family `{name}`", "", family.doc, ""]
    if family.shared:
        lines += ["Named the same in every mode:"] + [f"- `{key}`: {meaning}" for key, meaning in family.shared.items()] + [""]
    lines += [f"Modes (`\"kind\": \"{name}\", \"mode\": ...`):"]
    lines += [f"- `{mode}`: {spec.doc.split('. ')[0].rstrip('.')}." for mode, spec in family.modes.items()]
    return "\n".join(lines + [""] + [_mode_page(spec) for spec in family.modes.values()])


def _mode_page(spec: ModeSpec) -> str:
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


GUIDE_PARTS: Dict[str, Any] = {
    "overview": lambda: _OVERVIEW,
    "model": lambda: _MODEL,
    "reference": _reference,
    "expressions": lambda: _EXPRESSIONS,
    "macros": lambda: _MACROS.replace("MAX_ITEMS", f"{MAX_MACRO_ITEMS:,}").replace("MAX_DEPTH", str(MAX_MACRO_DEPTH)),
    "functions": _functions,
    "templates": lambda: _TEMPLATES.replace("FORMATS", ", ".join(f"`{f}`" for f in FORMATS)),
    "effects": _effects,
    "patterns": lambda: _PATTERNS,
    "mechanisms": _mechanisms,
    "running": lambda: _RUNNING,
    "checklist": lambda: _CHECKLIST,
}


def guide(part: Optional[str] = None) -> str:
    """The authoring guide (all parts), or one part: overview, model, reference, expressions, macros,
    functions, templates, effects, patterns, mechanisms, running, checklist — or a mechanism family
    (``guide("market")``) or one of its modes (``guide("market.auction")``)."""
    if part is None:
        rendered = [render() for render in GUIDE_PARTS.values()]
        mechanisms = list(GUIDE_PARTS).index("mechanisms")
        pages = [_family_page(name) for name, family in FAMILIES.items() if family.modes]
        return "\n\n".join(rendered[:mechanisms + 1] + pages + rendered[mechanisms + 1:])
    if part in GUIDE_PARTS:
        return GUIDE_PARTS[part]()
    if part in FAMILIES:
        return _family_page(part)
    family_name, _, mode = part.partition(".")
    if family_name in FAMILIES and mode in FAMILIES[family_name].modes:
        return _mode_page(FAMILIES[family_name].modes[mode])
    raise KeyError(f"unknown guide part '{part}' (parts: {', '.join(GUIDE_PARTS)}; families: {', '.join(FAMILIES)}; "
                   f"a mode as family.mode)")
