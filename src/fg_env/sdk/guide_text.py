"""The guide's hand-written prose parts (the generated ones are in :mod:`fg_env.sdk.guide_pages`)."""
from __future__ import annotations

__all__ = ["MODEL", "EXPRESSIONS", "MACROS", "TEMPLATES", "EFFECTS", "EFFECT_EXAMPLES", "RECIPES", "RUNNING",
           "INSPECT", "CHECKLIST"]

MODEL = """\
## How a run works

On a rounds clock: scheduled effects → feeds → events (phase start) → physics step → each stage in order →
events (phase end) → metrics sampled → invariants and end conditions checked. A run ends when
an `end` condition holds, an effect `end`s it, or `clock.rounds` is used up.
On a continuous clock, elapsed physics advances before scheduled effects, feeds and start events at the new boundary.

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
* `look` and `inspect` are free reads: up to `max_calls` of them per turn use no call, and one past that is refused
  without spending a call, so an agent can always still act. The same read twice in a turn answers "Unchanged".
* A stage with `actions: []` wakes nobody: use it as a pure resolution step (`on_enter`/`on_exit`).
* `must_act: true` removes `end_turn` while an action is available; `on_idle` effects run for each agent
  that ends a turn without acting (`$actor`) — a forfeit or a default move. An agent that could act and did not — in
  a `must_act` stage, or any stage once its calls ran out — is reported as an `idle` event ("Ben did not act.").
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
* Physics precedes agent stages. Coupled world and entity equations share intermediate integration states.
  Continuous time starts at zero without a fictitious initial physics interval. Failed intervals restore
  equation state and property writebacks.
* Lifecycle hooks: `types.X.on_create` / `on_remove` run for every entity of X (and its subtypes; an
  ancestor's hooks first) the moment it is created or removed — by an effect, a mechanism or a hook —
  inside that change, so a `fail` in a hook refuses it. Entities made at build run on_create once the
  whole world exists, in creation order (`on_create_at_build: false` skips them). `$it` is the entity;
  in on_remove it is already no longer alive. Hooks setting off hooks stop at 16 levels.
* Invariants are checked after every action and effect block (and after physics): write them for states that
  must hold at all times, not ones that only settle at the end of a stage. An agent's action that breaks one —
  itself or through the triggers and hooks its commit sets off — is refused and undone, and the agent is told the
  invariant's `why` (give one: without it the agent only hears that a rule would break); the run goes on and its
  diagnostics count it. A break by anything else (events, physics, the build) fails the run. `"check": "round"`
  checks one only at the end of every round (a conservation sum over a big crowd then costs one pass a round, not
  one per change) — a break found then fails the run, whatever caused it; `"check": "end"` once, when the run
  finishes.
* An agent's action is one undoable unit: the checks of its call (requirements, arguments), its effects, and the
  hooks and triggers its commit sets off. A rule that fails anywhere in it (a division by zero, a number too large) refuses and undoes that action
  alone — in a sealed stage when the choices commit, in an atomic turn the whole turn — and the agent is told the cause
  without the rule or any hidden value. The run goes on; `result.diagnostics` names the failing rule with a fix and
  `stats.faulted_actions` counts these refusals. Guard such rules (`min`/`max` on the parameter, or a `when` with a
  `why`) so agents are told the limit up front. The same failure in events, world logic or physics fails the run, as
  do a host that fails and a crash in a mechanism's own code, wherever they happen.
* `end` conditions are checked after the start events, after each stage, and at the end of the round.
  `"check": "action"` also checks one the moment anything commits — an action, a sealed choice, an event or
  hook's effects — so a winning move ends the run before the next agent moves (in any kind of stage; sealed
  choices commit in turn order, so later ones are not applied). The `end` effect inside an action does the same.

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

EXPRESSIONS = """\
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
  $clock $round $stage $metrics $series $arm $viewer $event $outer $pending $result.
* Contract `defs` are called like built-ins: `$utility($actor, $params.offer)`.
* Bare words are text even when they match a property name: write `$actor.bet`, not `bet`.
* Strict: unknown props, missing roots and type errors are errors, never silent zeros.

Roots available by location (plus everywhere: $inputs $world $physics $clock $round $stage
$metrics $series $arm):
ROOTS_TABLE

`$clock` fields: round rounds left unit date label. `$metrics.x` = latest value; `$series.x` = list per round.
"""

MACROS = """\
## Macros (repeat structure from data)

An object with `for` and `make` is a macro (data with only a `make` field, like a car's make, is not): it repeats `make` once per value, replacing `{name}`
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

TEMPLATES = """\
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

EFFECTS = """\
## Effects (actions.do/otherwise, events.do, stages.on_enter/on_exit)

Assignment text:
* `"$actor.cash -= $params.qty * $params.offer.price"` — also `=`, `+=`, `*=`, `/=`; targets are
  entity props (`$actor.x`, `$params.offer.x`, `$it.x`), `$world.x`, `$physics.x`.
* `"$total = $params.qty * 2"` — a local (`$total`) usable by later effects and the outcome.
* `+=`/`-=` on a list prop append/remove an item.
* Element assignment: `"$world.board[$i] = $actor.mark"`, `"$actor.scores[round_2] += 1"` (lists and maps).
* Links: `"$link($actor, $params.who, trusts).value += 0.1"`, `"$link($actor, $params.who, trusts).since = $round"`
  (the link must exist; its value is clamped to the relation's min/max, fields are typed like props).
* A write past a numeric prop's min/max is refused, like a transfer that does not fit: an action is rolled
  back and its actor told why; an event is refused and logged. To saturate, say so: `$clamp(x, low, high)`.
  Types are enforced.

Operation objects (exactly one operation key each):
OPS

### Ordered processing

`each` has no `order` field. Pass a sorted collection to `each` instead:

```json
{"each": "$sort(item, [$it.due, $it.sequence])", "do": "$world.seen += $it.id"}
```

Keys are compared in order: earliest `due`, then lowest `sequence`. Use list keys
for priority and tie-breaking; multiplying a key by a large constant can change
priority when the second key grows. Negate a numeric key for descending order.
The list is selected once before the loop; effects still see current entity
properties. Filter with `$filter` when needed. For one highest-ranked item use
`$best(items, [key1, key2])`; exact ties return a list by default, so use a unique
last key when the rule requires one deterministic item.
"""

EFFECT_EXAMPLES = {
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
    "repeat": '{"repeat": "$max(1, $count(order))", "while": "$count(order) > 1", "do": [...]}  (limit may be an expression; derive it from the data, not an arbitrary constant; error if still true at the limit)',
    "block": '{"block": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}  (runs a named effect list from `blocks`)',
    "chance": '{"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails", "do": [...]}], '
              '"as": "coin"} or {"chance": "deal", "outcomes": "$world.deck", "weight": "1", "as": "card", "do": [...]}  '
              '(picks one outcome from the listed distribution, logged as a `chance` event; `fg_env.game` can '
              'enumerate and choose outcomes instead of sampling them)',
}

RECIPES = """\
## Recipes for common mechanics

* Configurable objects: declare `inputs.shop` with `type: map`, `default: {}` and `fields` mapping
  names to InputSpecs. A table uses `fields` for each row; a list uses `items` for its element spec.
  Field defaults are filled at load; required fields and bounds are validated before execution.
  Bind these values explicitly through `$inputs.shop.price`, entity defaults or population `from`.
  Choose `label`, `description`, `unit` and `display` (text, textarea, select, toggle, date, number,
  slider, knob, table, object, list, json) for the host UI. Sliders/knobs require numeric min < max.
  Bounds reject values: only add limits justified by the brief or domain, never to fit a control.
  Otherwise use `display: number`. `step` suggests a control increment, not a validation rule.
* Data files: `"inputs": {"households": {"type": "table", "source": "households.csv", "columns": {"income":
  "number", "size": "int"}}}` then `"population": [{"type": "person", "from": "$inputs.households"}]`. Files are
  read from the contract's folder (or `data_dir=`); undeclared CSV columns stay text. Also `.json` and `.jsonl`.
  Read a big table by key, not by scanning it per row: `$lookup($inputs.sales, sku, $row.sku)` (rows, indexed once
  per run; fields and keys may be lists) and `$lookup_one($inputs.models, model, $row.model)`. A queue served once per
  arrival is a world list of ids (`$world.queue += $made.id`), not a `$count(call, …)`; `check` warns about both scans.
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
* Voting: the `decision` family — `{"kind": "decision", "mode": "ballot", "who": "voter", "options": [...]}` adds
  the vote tools, a sealed stage and the tally (`$world.<name>_result.winner`); `mode: deliberation` adds motions
  and debate (guide('decision.ballot')).
* Deliberation: records (`chat`) + a sequential stage with `until: "$all(member, $it.ready)"`
  and `quiet: skip`; a `say` action posts and clears readiness.
* Hidden roles: the `groups` family — `{"kind": "groups", "mode": "roles", "who": "player", "deck": {"werewolf": 2,
  "villager": "rest"}, "teams": {...}, "know": [...]}` deals private roles, tells teammates, gates role actions and
  eliminates and reveals players (guide('groups.roles')). An entity's built-in `alive` turns false only when it is
  removed; a player the mechanism eliminates stays in the world with its `living` prop false.
* Hidden information: `private` props (hidden from others' inspect), per-type views, record
  `visible` rules, `to` on posts/emits, `private: true` actions (no announcement). Visibility shapes only what an
  agent is shown or offered (brief, updates, views, tool choices, outcome text, its policy); game logic — action
  `when`/`do`, events, triggers, stages, `end`, metrics, outputs, invariants — reads every record entry and event,
  so an auditor's `accuse` can count messages it never saw. To ask what one agent can see inside logic, filter
  explicitly: `$records(chat, $it.author == $actor or $actor.id in ($it.to or []))`.
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
  (`"noise": "sigma*price"`, Itô noise drawn from stable entity/variable seed paths).
  Drift refines automatically with `rtol`/`atol`; general noise refines the same Brownian path with `noise_rtol`.
  Supported independent affine processes use exact transitions. Convergence failure stops the run.
* Per-entity dynamics (viral load, firm capital, habit strength): `"physics": {"per": {"person": {"vars":
  {"viral_load": {"rate": "growth*viral_load - immunity*viral_load", "noise": "0.2*viral_load"}},
  "read": {"exposure": "$count($neighbors($it, contact), $it.sick)"}, "write": {"sick": "viral_load > 5"}}}}`.
  Every person integrates its own number props; rates read its number props, the type's `params` and
  `read`s (per entity, over `$it`) and world physics names. `where` limits who integrates this step.
  Entities couple through `read`; intermediate states are shared rather than held fixed for the round.
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
* Calendars: `clock.start` with unit day, week, month, year, hour or minute adds the date to the time label, and may
  read an input (`"start": "$inputs.start"`) so each backtest case carries its own dates; `$clock.date` is today,
  `$clock.start` round 1. `$date_add(d, 1, month)`, `$days_between(a, b)`, `$date_part(d, week)` (weekday, month,
  quarter, …) and `$is_holiday(d, $inputs.holidays)` do the arithmetic; narratives name rounds in the clock's unit.
* Policy rules with `each` act once per item: `{"each": "$filter(army, $it.owner == $actor.id)",
  "do": "hold", "with": {"army": "$it"}}`.
* Coded participants: `policies` rules (first legal matching rule wins) for crowds and baselines;
  set `types.X.policy` to make them the default.
"""

RUNNING = """\
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
print(fg_env.report(exp, contract="shop.json", objective="max:profit", require={"fill_rate": ">= 0.95"}))
# plain words for an owner: the choice and its outcome with 80% ranges, drivers, risks, assumptions, fit to data
# (audience="analyst" adds the method; validation=, a run, a sweep or a validation also work; fg-env report file.json)
fg_env.sweep("shop.json", {"price": {"low": 1, "high": 5, "steps": 5}}, runs=10).table()   # also sensitivity, calibrate, backtest
# every check, experiment and analysis reads data files beside the contract file (or data_dir=) and takes hosts=
v = fg_env.validate("shop.json", [{"name": "Q1", "inputs": {"start": "2026-01-05"}, "actuals": {"units_by_sku": {...}}}],
                    runs=20, season=4); print(v.report())   # bias, MAPE/WAPE per key, interval coverage, baselines
cal = fg_env.calibrate("shop.json", cases, {"demand_scale": {"low": 0.5, "high": 2}})   # cases: {name, inputs, targets}
# a rate per case: {"value": 0.03, "count": calls} weighs it by its data; "pool": true matches the cases together
fg_env.validate("shop.json", cases, uncertainty=cal)   # also experiment, sweep, backtest: draw params per run
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
`{"texts": {hash: text}, "wakes": [...], "chance": [...]}`: brief, update and view hashes and sizes, news event sequence
numbers, tools offered, every call with its arguments and result, timeouts and undone turns — every text
stored once. `$seen(agent, item)` asks whether an agent was shown an event, a record entry or a view by name;
a contract that uses it records exposures automatically. `experiment`, `tournament`, `evaluate` and `run_jobs`
take `exposures=True` too, every run keeping its own (`--exposures` with `--json`). `result.frames` and
`env.spectate()` give the spectator views (`fg-env run file.json --frames frames.json` saves them).

Traces: a run with `exposures=True` is a trace (`fg-env run file.json --trace run.jsonl`); `result.save("run.json")`
or `.jsonl`, `fg_env.RunResult.load(path)`. `t = fg_env.trace(result_or_file)`: `t.overview()` (per agent: turns,
calls, invalid rate, timeouts, tokens), `t.turn(7)` or `t.turn("ana", 3)` (what the agent read, the tools offered,
every call with its result), `t.timeline("ana")`, `t.search("bribe")` (in what agents read or wrote), `t.invalid()`
(refused calls with the correction given), `t.agent("ana")`; each has `.data` and prints as text
(`fg-env trace run.jsonl turn ana 3`). `t.replay("shop.json")` runs the contract again offline with the recorded
calls (`fg_env.participants.replay(t)`) and host answers, and reports the first divergence — a turn, the brief or
update text, the tools offered, a call result, an event or the ending; `fallback="policy:x"` plays on after it
(`fg-env trace run.jsonl replay shop.json`, exit 1 on a divergence). Each wake's `steps` are what it replays.
Outcomes a `chance=` chooser picked are recorded (`exposures.chance`) and replayed without it (a changed chance
node is a divergence); a forked run replays from the snapshot it continued from (`exposures.start`).
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
limit that ran out; snapshots keep it. `experiment` (with `branch_at` the shared rounds count toward each arm),
`tournament`, `evaluate` and `run_jobs` give every run the whole budget, as `--budget tokens=200000` does on
`fg-env run`, `experiment`, `tournament` and `evaluate`. Usage reported after a turn ran out of time still counts.
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
fg_env.conformance("kuhn_poker.json", sims=20).summary()   # legal calls, chance, clone, serialize, returns, replay, resume, leaks
print(fg_env.playthrough("kuhn_poker.json", seed=1))       # every seat's reading at every decision: a golden text to diff
from fg_env.sdk.game.algorithms import CFRSolver, exploitability, minimax, MCTSBot
policy = CFRSolver(game, plus=True).iterate(1000).average_policy(); exploitability(game, policy)
fg_env.run("tic_tac_toe.json", {"x": "mcts:200", "o": "minimax"})   # also "ismcts:200", "cfr:policy.json", "cfr:1000"
aec = fg_env.pettingzoo_aec("kuhn_poker.json", seed=1)     # PettingZoo AEC (reward since last turn); pettingzoo_parallel too
```
Games transform into ordinary contracts: `fg_env.sdk.game.repeated(contract, 10)`, `misere`, `zerosum`;
`game.start_at(steps)` starts part-way. Known-answer games live in `examples/contracts/games`.
CLI: `fg-env conformance file.json --sims 50`, `fg-env playthrough file.json --seed 1 [--check golden.txt]`,
`fg-env bench --game file.json`.
A copy is rebuilt from the run's base and replays what its participants did, so it is exact (state, random
streams, turn numbers, log, recorded host answers) and costs a restore plus the round so far; turn time
limits never run out in a copy. It holds the whole world, hidden state included. Game action ids are fixed
when the game is created (one per combination of listed argument values; free text and lists are
parametric: apply them as `{"tool", "args"}`). `fg_env.load(..., chance=callable)` chooses chance outcomes.

LLM participants: `fg_env.participants.anthropic(anthropic.Anthropic(), "claude-sonnet-5")` or
`fg_env.participants.openai(client, model)`; they cache the brief and loop over tool calls, retry rate
limits, timeouts and server errors (`retries=4`), then fail the run or, with `on_error="end_turn"`,
forfeit the turn. Both take `max_tokens` (openai also `reasoning_effort`); a reply cut off at the limit
counts in `truncated` and, when it called no tool, is asked once for a short tool call (`retry_truncated`).
Every truncated reply wastes its whole output: for frequent decisions use `reasoning_effort="low"` (in a Hold'em
evaluation it cut cost by 38% with no visible loss in play), or keep the default effort with a larger `max_tokens`
(6,000 was cut off 9 times in 96 turns).
Their real token usage is in `result.stats` (`llm_calls`, `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `llm_retries`, `forfeits`, `truncated`); your own
participants can add theirs with `wake.record_usage(...)`.
Built-ins: `"random"`, `"idle"`, `"policy:<name>"`, and game algorithms `"mcts:N"`, `"ismcts:N"`, `"minimax[:depth]"`, `"cfr:<policy.json|iterations>"`.

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

INSPECT = """\
## Inspecting a run

```python
result = fg_env.run("game.json", seed=1)     # random agents; {"player": my_agent} for yours
print(result.summary())                       # status, winner, outputs, output issues, diagnostics
result.outputs, result.winner, result.ended_by, result.metrics, result.series["price"]
```
Summaries show numbers to 4 decimals; an output's `"format": "money"` (any template format) shows it that way.
Stored values stay exact.

`result.diagnostics` is `[{code, path, message, fix}]`: logic problems the run revealed. It reports:
* a tool offered when none of its choices could succeed;
* sealed choices that overwrite each other's values;
* an agent type that never had an action it could take;
* a stage that can never run, or a measure that reads only what no rule changes;
* with model participants, an action that was mostly refused.

`fg-env check` reports the smoke round's diagnostics as warnings; `--rounds 5` plays longer for more evidence.

`result.events` is the log in order: `{seq, round, stage, kind, actor, text, data}`. Its kinds are `action`,
`outcome` (a sealed choice's result), `record`, `news`, `refused`, `timeout` and `end`. For example:
`[e.get("text") for e in result.events if e["round"] == 3]` (keys without a value are left out).

What agents saw:
* `env.preview("ann")` shows the next turn exactly as ann will get it.
* A recorded run holds every turn: `result = fg_env.run(c, seed=1, exposures=True)`, then `t = fg_env.trace(result)`.
* `t.overview()` gives turns, calls, invalid rate and tokens per agent.
* `t.turn("ann", 3)` shows what ann read, the tools she was offered, and every call with its result.
* `t.invalid()` lists refused calls with the correction given; `t.search("bribe")` searches the text.

Reproducing: the same seed and participants give the same run. `result.save("run.jsonl")` and
`fg_env.RunResult.load(path)` keep a run. `t.replay("game.json")` runs the contract again with the recorded calls
and names the first divergence.

CLI: `fg-env run game.json --seed 1 --events --trace run.jsonl`, `fg-env trace run.jsonl turn ann 3`.
"""

CHECKLIST = """\
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
  `result.stats` (invalid_rate and avg_update_tokens should stay low; faulted_actions should be 0).
"""
