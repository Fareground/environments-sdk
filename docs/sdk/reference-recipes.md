# recipes

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
* Clinics, queues, trading days: a round is a stretch of time (`"clock": {"unit": "minute", "step": 30}` for
  half-hours); the `operations.queue` mechanism plays each interval's arrivals natively, and a stage `when`
  (`"$round % 7 == 1"`) runs a step only on some rounds.
* Money & trade: number props + `transfer` (atomic, never negative). Invariants like
  `"$all(trader, $it.cash >= 0)"` guard the books.
* Markets / order books: orders as entities (`create` with side, price, qty, owner); a `round.end`
  event matches with `repeat` while best bid ≥ best ask using `$top`/`$sort`; trades update
  holdings and `remove` filled orders.
* Voting: the `decision` family — `{"kind": "decision", "mode": "ballot", "who": "voter", "options": [...]}` adds
  the vote tools, a sealed stage and the tally (`$world.<name>_result.winner`); `mode: deliberation` adds motions
  and debate (guide('decision.ballot')).
* Deliberation: records (`chat`) + a sequential stage with `until: "$all(member, $it.ready)"`
  and `quiet: skip`; a `say` action posts and clears readiness.
* Answers before something takes effect (an exhibit offered → objection → ruling → admitted or excluded; a
  motion and its amendments; a spell and its counter): a procedure `stack` (guide('decision.procedure')). The offer
  only pushes an item; the agents its kind names answer it; items resolve last in, first out, so the ruling
  resolves before the objection and the objection (countering the offer when sustained) before the offer.
  `"stack": {..., "stage": "exam"}` holds the answers in the examination stage itself, so one stage runs many
  offers a round: `"who": "$it.id == $world.examiner and $stack(trial, top) == null or $stack(trial, waiting,
  $it)"` with an `until` for when the examination is over. A `wake` with `now` answers what already happened.
* Repeating a group of stages (negotiate → vote until ratified; deliberate → ballot until unanimous or the last
  ballot): make each round one pass of the group — `"stages": [talks, {"name": "vote", "when": "$world.called"}]`
  with an `end` condition, and `clock.rounds` as the most passes. When the groups differ (deliberation, then a
  ballot, then back), use procedure phases whose `next` loops (`{"to": "deliberation", "when": ...}`).
* Hidden roles: the `groups` family — `{"kind": "groups", "mode": "roles", "who": "player", "deck": {"werewolf": 2,
  "villager": "rest"}, "teams": {...}, "know": [...]}` deals private roles, tells teammates, gates role actions and
  eliminates and reveals players (guide('groups.roles')). An entity's built-in `alive` turns false only when it is
  removed; a player the mechanism eliminates stays in the world with its `living` prop false.
* Hidden information: `private` props, per-type views, record `visible` rules, `to` on posts/emits,
  `announce: false` on actions (nobody else learns they happened). Agents get `inspect` only for types that set `inspect`.
  A `private` prop is hidden from every agent but its owner: an agent owns its own; the world's and any other
  entity's are hidden from every agent unless a view's or entity choice's `where` picks the items by the reader and a
  prop of theirs (`$it.owner == $actor.id`) — the reader owns what it picks (by id names no owner). Reading a hidden
  value in anything worked out for one agent (views and their where/sort/attach, tool choices, bounds and defaults,
  outcome text, briefs, policies, defs they call, series outputs worked out from private props) is an error at run time,
  however it is spelled; so is a stage `order` that reads one, since every agent sees the turn order, and a `who` in a
  stage whose actions are announced. Reveal what an agent may learn by working it out in game logic
  (`"do": ["$seen = $params.target.role"], "outcome": "... {$seen}"`, or a prop the agent owns). Text sent to
  several agents — an `announce`, an event's `say`, an emit's `say` without a lone `to` — may read no
  private prop, not even the actor's: reveal it the same way (`"$shown = $actor.card"`, then `{$shown}`).
  A public fact about private data (how many cards a hand holds) is a public prop the rules keep up to date: write it
  wherever the private one changes (`"$actor.cards = $len($actor.hand)"`).
  The engine's own refusals (a transfer that does not fit, a bound) never show a hidden value. A `when` that reads a
  hidden value does not hide the tool: it stays listed and a call is refused (and spent) when the `when` fails. A
  non-agent entity reaches agents only through what the contract shows, so a prop the views already gate needs no
  `private`. An entity's type is public (inspect names it): keep a secret role in a private prop, not a subtype. A refusal
  is information too — a `when` or `fail` that reads hidden state tells the actor something about it. Visibility
  shapes only what an agent is shown or offered (brief, updates, views, tool choices, outcome text, its policy); game logic — action
  `when`/`do`, events, stages, `end`, metrics, outputs, invariants — reads every record entry and event,
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
  `{"layer": "scent", "diffuse": 0.1}` and `{"layer": "scent", "decay": 0.05}`. A set past the layer's min/max is
  refused like a prop's (saturate with `$min`/`$clamp`); diffuse and decay stay within it. Layers are kept in snapshots.
* Cellular automata and simultaneous updates: an `each` loop with `"sync": true` — every item's rules read the
  world as it was before the loop and all writes land together (Game of Life is one event: `{"on": "round.end",
  "do": [{"each": "cell", "sync": true, "do": ["$n = $count($near($it, 1), $it.on)", "$it.on = $n == 3 or ($it.on
  and $n == 2)"]}]}`). Items in random order: `"each": "$shuffle(ant)"`; by a key, lowest first:
  `"each": "$sort(order, $it.price)"`.
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
* Continuous dynamics: the `physics` mechanism (`"mechanisms": {"physics": {"kind": "dynamics", "mode": "ode",
  ...}}`, guide('dynamics.ode')): vars with rates (math over bare names), `read` from the world,
  `write` back to props; effects adjust `$physics.x` (policy shocks). `noise` adds a random term
  (`"noise": "sigma*price"`, Itô noise drawn from stable entity/variable seed paths).
  Drift refines automatically with `rtol`/`atol`; general noise refines the same Brownian path with `noise_rtol`.
  Supported independent affine processes use exact transitions. Convergence failure stops the run.
* Per-entity dynamics (viral load, firm capital, habit strength): the physics mechanism's `"per": {"person": {"vars":
  {"viral_load": {"rate": "growth*viral_load - immunity*viral_load", "noise": "0.2*viral_load"}},
  "read": {"exposure": "$count($neighbors($it, contact), $it.sick)"}, "write": {"sick": "viral_load > 5"}}}`.
  Every person integrates its own number props; rates read its number props, the type's `params` and
  `read`s (per entity, over `$it`) and world physics names. `where` limits who integrates this step.
  Entities couple through `read`; intermediate states are shared rather than held fixed for the round.
* Latency and lossy channels: `"delay": 2` on `post`/`emit` delivers the message 2 rounds
  later with its content as it was when sent; `"drop": 0.1` loses it, rolled from the
  run's seed when sent. A refused action sends nothing. Entries carry the round they arrive.
* External data (prices, news, weather): a `host.feed` mechanism, `"oil": {"kind": "host", "mode": "feed", "host":
  "market", "into": "world.oil_price", "query": {"symbol": "BRENT", "date": "{$clock.date}"}, "fallback":
  "$world.oil_price * $uniform(0.98, 1.02)"}`,
  or `"into": "records.news"` for entries. Bind the host when loading: `fg_env.load(path, hosts={"market":
  adapter})`, where the adapter is any object with `fetch(request)`; `fg_env.host.adapters.historical(rows,
  at="date", value="close")` replays a price history for backtests. Answers are recorded on the host tape:
  snapshots, restores and replays never ask again, and host text reaches agents «quoted».
* Scenarios & experiments: `inputs` for scenario knobs, `arms` for variants (input overrides or
  patches), events with a `when` for shocks (`"$round == 10 and $arm == 'shock'"`, `"$chance(p)"`); `fg_env.experiment` runs arms
  with shared seeds (`branch_at=N`: every arm continues from one shared history of N rounds).
* Games: a player type's `score` names the seats and what each scores (`"types": {"player": {..., "score":
  {"seat": "$it.seat", "value": "$it.chips - 10", "utility": "zero_sum"}}}`); dealt cards and dice as `chance`
  effects (`{"chance": "deal", "outcomes": "$world.deck", "as": "card", "do": [...]}`) so solvers can
  enumerate them; `must_act` stages so a seat cannot stall; `step` on number params so bids have ids.
* Families of agents: `types.trader` with shared props, then `types.market_maker: {"extends": "trader"}`;
  `$count(trader)`, `by: trader`, views `for: trader` and `brief.roles.trader` cover every kind.
* Bookkeeping on birth and death: `"events": [{"on": "create.firm", "do": ["$world.firms_founded += 1",
  {"link": "supplies", "from": "$it", "to": "$top(supplier, $it.capacity, 1)[0]"}]}, {"on": "remove.firm", "do":
  [{"each": "$filter(job, $it.employer == $outer.id)", "do": [{"remove": "$it"}]}]}]` — every firm, however it was
  created, is counted and connected; closing one lays off its jobs.
* Reusable logic: `defs` — a formula (`"utility": {"args": ["side", "offer"], "expr": "..."}`, read as
  `$utility(...)`) or an effect list (`"match": {"args": ["order"], "do": [...]}`, run with
  `{"call": "match", "with": {"order": "$made"}}`).
* Inspection: `types.X.inspect: true` (or an expression over `$viewer` and `$it`) gives agents an `inspect` tool for
  those entities, showing every prop that is not `private`. Without one it is not offered (its only choice would be
  the agent itself: show an agent's own state in a view).
* Boards and tables in views: `"bullet": false` prints lines without "- ". View titles are templates.
* Participants keyed by a parent type (`{"tier": ...}`) and `policy` on a parent type reach every subtype.
* Calendars: `clock.start` with unit day, week, month, year, hour or minute adds the date to the time label, and may
  read an input (`"start": "$inputs.start"`) so each backtest case carries its own dates; `$clock.date` is today,
  `$clock.start` round 1. `$date_add(d, 1, month)`, `$days_between(a, b)`, `$date_part(d, week)` (weekday, month,
  quarter, …) and `$is_holiday(d, $inputs.holidays)` do the arithmetic; narratives name rounds in the clock's unit.
* Policy rules with `each` act once per item: `{"each": "$filter(army, $it.owner == $actor.id)",
  "do": "hold", "with": {"army": "$it"}}`.
* Coded participants: `types.X.policies` rules (first legal matching rule wins) for crowds and baselines;
  set `types.X.policy` to make one the default.

