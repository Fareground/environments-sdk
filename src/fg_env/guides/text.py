"""The guide's hand-written prose parts (the generated ones are in :mod:`fg_env.guides.pages`)."""
from __future__ import annotations

__all__ = ["MODEL", "EXPRESSIONS", "TEMPLATES", "EFFECTS", "EFFECT_EXAMPLES", "RECIPES", "RUNNING",
           "INSPECT", "CHECKLIST"]

MODEL = """\
## How a run works

Every round: scheduled effects → feeds → `round.start` events → physics step → each stage in order →
`round.end` events → metrics sampled → invariants and end conditions checked. A run ends when
an `end` condition holds, an effect `end`s it, or `clock.rounds` is used up.

Events are the world's logic outside turns, one list: `on` is when an event is considered, `when` whether it
fires, `do` what it does (atomically), `say` the headline agents get as news, `once` at most once per run.
* `round.start` (the default) and `round.end`: every round. A schedule is a condition: `"$round == 5"`, `"$round in
  [1, 3]"`, `"$round % 7 == 1"` (every 7 rounds from round 1), `"$chance(0.1)"`, `"$arm == 'treatment'"`.
* `stage.<s>.start` when stage s starts (only if its `when` holds), `stage.<s>.end` after it (a simultaneous stage's
  choices have committed: resolve them here), `stage.<s>.turn` after each agent's turn in it, with `$actor`, `$acted`
  and `$timed_out` (a forfeit or default move: `"when": "not $acted"`).
* `create.<type>` / `remove.<type>`: inside the change that creates or removes an entity of the type (or a subtype;
  an ancestor's events first), with `$it` (in `remove`, already no longer alive), so a `fail` refuses that change.
  Entities made at build fire `create` once the whole world exists, in creation order (`$round` is 0 then).
* `change`: after every commit (an action, a sealed choice, an effect block, physics, the round's end), the moment
  `when` becomes true; it fires again only after it was false.
Events on one anchor fire in the order written, the author's before those mechanisms generate. A round event whose
`do` is one `each` loop runs item by item: each item has luck of its own, and invariants are checked once all ran.

A stage wakes agents (`who`, in `order`). An agent's turn is a short session: it reads its
brief + update, calls tools (legal actions, `look`, `inspect`, `end_turn`) until it ends the
turn, uses `max_actions`, or runs out of `max_calls`.
* `turns: sequential` — one agent at a time; actions apply immediately and the tool result is
  the actual outcome.
* `turns: simultaneous` — everyone sees the same state; actions are submitted, then committed one agent
  after another once all have chosen (sealed bids, votes, simultaneous moves). Outcomes arrive as news.
  Without an `order`, choices commit in a random order drawn from the seed each time, so when two agents
  take the last item, either may get it. A choice is tried at submit after the agent's own earlier
  choices in the stage (two buys cannot spend the same coins).
* Choices that decide together (highest bid wins, pro-rata fills, rock–paper–scissors): the action only
  records the choice (`"announce": false, "do": ["$actor.bid = $params.amount"]`) and an event on
  `stage.<s>.end` resolves them all at once: `"$top = $max(bidder, $it.bid)"`, `"$winner = $choice($filter(bidder,
  $it.bid == $top))"` (ties at random); pro rata: `"$fill = $min(1, $world.stock / $max($sum(buyer, $it.want),
  1))"`, `{"each": "buyer", "do": ["$it.got = $it.want * $fill"]}`. Clear the recorded choices on
  `stage.<s>.start`.
* `until` repeats passes within the round (deliberation until everyone is ready).
* `quiet: skip` skips agents with nothing new since their last turn (from the second pass on;
  the first pass always wakes everyone).
* `look` and `inspect` are free reads: up to `max_calls` of them per turn use no call, and one past that is refused
  without spending a call, so an agent can always still act. The same read twice in a turn answers "Unchanged".
* A stage with `actions: []` wakes nobody: use it as a pure resolution step (events on its `start`/`end`).
* A stage without `actions` offers every action. When other stages list their own, list this stage's too
  (check warns otherwise: agents could take another phase's actions here), or write `"actions": "all"`.
* `must_act: true` removes `end_turn` while an action is available. An agent that could act and did not — in
  a `must_act` stage, or any stage once its calls ran out — is reported as an `idle` event ("Ben did not act.").
* `terminal` may be an expression checked after the action applies (`"$world.jump_finished"`), so a
  move can end the turn only sometimes (multi-jumps).
* `env.run(..., time_limit=30)` gives each agent wall-clock seconds for its turn. Past it the turn ends, later calls
  are refused, a `timeout` event is logged and the stage's `turn` events see `$timed_out`. The agent's update says
  how long it has. A participant that finishes in time plays exactly as it would without a limit.
* `valid` makes a turn's actions apply together: each applies at once (the agent sees its move), but events,
  reactions and invariants wait until the turn ends, and an action's own `outcome` text (and attached files) is
  shown once the turn commits — an undone turn shows nothing it was not charged for. Its conditions (`$actor`,
  `$pending`) are checked when a turn that acted ends; if one fails, every action of the turn is undone, the agent is
  told `why` and plays the turn again (castling through check, a full backgammon move); `"valid": "true"` makes
  turns atomic with no condition. An action that draws randomness settles the turn so far at once, so later actions
  cannot undo its luck (if `valid` fails then, the turn is undone and over). In a simultaneous stage each agent's
  choices commit or are undone together.
* Luck never decides whether a call is allowed: `when` requirements and parameters' bounds, defaults, values and
  `where` may not draw at random (a check error), since a refused call costs nothing and calling again would roll
  fresh luck. Draw in `do`: a call that drew has been played, even when a rule then fails. A view's
  randomness is fixed for the turn, so looking again shows the same noisy signal (and a preview shows the turn's).
* Views with `"for": "spectator"` are an omniscient picture for UIs and reports: rendered at the end of
  every round into `result.frames` (the last marked `final`) and on demand by `env.spectate()`, never
  shown to an agent. They have no `$actor`; randomness they draw never changes the run.
* Physics precedes agent stages. Coupled world and entity equations share intermediate integration states.
  Failed intervals restore equation state and property writebacks.
* Invariants are checked after every action and effect block (a round event's `each` once its last item ran, or
  before a `change` event or reaction an item sets off) and after physics: write them for states that must hold at all times, not
  ones that only settle at the end of a stage. `$all(<type>, <condition>)` whose condition reads only each member's
  own properties and `$inputs` re-checks only the members a change touched, so it stays cheap in any crowd. An
  agent's action that breaks one — itself or through the events its commit sets off — is refused and
  undone, and the agent is told the invariant's `why` (give one: without it the agent only hears that a rule would
  break; it is a template, which may read no agent's private prop); the run goes on and its diagnostics count it. A break by anything else (events, physics, the build) fails
  the run. `"check": "round"` checks one only at the end of every round (a conservation sum over a big crowd then
  costs one pass a round, not one per change) — a break found then fails the run, whatever caused it;
  `"check": "end"` once, when the run finishes.
* An agent's action is one undoable unit: the checks of its call (requirements, arguments), its effects, and the
  events its commit sets off. A rule that fails anywhere in it (a division by zero, a number too large) refuses and undoes that action
  alone — in a sealed stage when the choices commit, in an atomic turn the whole turn — and the agent is told the cause
  without the rule or any hidden value. The run goes on; `result.diagnostics` names the failing rule with a fix and
  `stats.faulted_actions` counts these refusals. Guard such rules (`min`/`max` on the parameter, or a `when` with a
  `why`) so agents are told the limit up front. The same failure in events, world logic or physics fails the run, as
  do a host that fails and a crash in a mechanism's own code, wherever they happen.
* `end` conditions are checked after the start events, after each stage, and at the end of the round, so
  `$round == <clock.rounds>` ends the run before the last round plays (check warns): the run ends after its last
  round by itself; to name a winner then, use an `end` effect in a `round.end` event.
  `"check": "action"` also checks one the moment anything commits — an action, a sealed choice, an event's
  effects — so a winning move ends the run before the next agent moves (in any kind of stage; sealed
  choices commit one after another, so later ones are not applied). The `end` effect inside an action does the same.

What an agent reads:
* brief (static, cacheable): name, situation, rules, its identity and role text.
* update: time label and stage, why it is acting ("Your turn again." only when it already had a turn in this stage
  this round), "Since your last turn" ("So far" on its first turn; announcements of
  others' actions, outcomes of its own simultaneous actions, record entries, event news; in a busy round what is
  addressed to it is always shown, then the newest news, then the newest of others' actions, and the rest counted),
  then every declared view that applies. Text written by participants is wrapped «like this».
* tools: one per legal action with a JSON Schema (entity choices as enums, numeric bounds when
  they depend only on the actor), plus look/inspect/end_turn. Invalid calls return what to fix. An action's name is
  its tool's name, so it must be one providers accept (letters, digits, _ and -, at most 64) and not a built-in's.

Unless an action sets `announce` (a template, or `false`: nobody else learns it happened), others read a default line
"Name: action (args)." — in a simultaneous stage only "Name: action." (sealed choices stay sealed
unless `announce` reveals them), and without the arguments the action writes into a private property;
an action that posts to a record announces nothing extra (the entry is the news). Text an agent types (text params) keeps its provenance wherever it is stored and
always renders «quoted» on one line, in news, views and outcomes.

An action applies atomically: if any effect `fail`s or a `transfer` lacks funds, every change
is rolled back and the agent is told why. World logic (events) has no one to refuse: the same
failure there fails the run at its path, so guard such a block with an `if`. A refusal that rolled luck or whose rules
read a value hidden from the actor (a `when`, a `fail`, an error, a transfer) spends the action (a wrong guess at a
hidden code is a guess); any other refusal — a taken cell, bad arguments — costs nothing. Contract errors (bad expression at run time) stop
the run with status `failed` and the path of the broken rule.
"""  # noqa: E501 — guide text: each line is shown as written

EXPRESSIONS = """\
## Expressions

Any string containing `$name` is an expression; other strings are literal text.
* Roots: `$actor`, `$params`, `$it`, `$inputs`, `$world`, … (which ones depend on where — see below).
* Functions: `$count(buyer, $it.cash > 0)`. Per-item arguments bind `$it` (and `$i`).
* Bare words are text: `$actor.status == open`, `$count(offer)`. `true false null` are literals. A condition that
  is only a word (`"when": "deal"`) is text, always true: write `$world.deal`.
  Quote text with spaces: `$actor.mood == 'very happy'`. Any word may name a type, entity, property or item
  (`class`, `from` and `def` too) except the language's own `and or not in if else true false null`.
* Operators: `+ - * / // % **`, `== != < <= > >=`, `and or not` (`&& || !`), `in`,
  `a if cond else b`, lists `[1, 2]`, indexing `$top(offer, $it.price, 1)[0]`.
* Entities expose `id name type alive at` and their props. Comparing an entity with an id works.
* Maps: `{wage: 3, 'job years': 2}`; read with `.key` or `$get(map, key, default)`. Keys are text, as in JSON:
  `{1: 3}` holds the key `'1'`, which `$get(m, 1)`, `m[1]` and `1 in m` all find.
* Nested per-item functions rebind `$it`; the enclosing item is `$outer`:
  `$sum(trader, $sum(order, $it.qty, $it.owner == $outer.id))`.
* Every function call needs its `$`: `$max(a, b)`, never `max(a, b)`.
* `a or b` gives the first truthy value (a default: `$x or 0`); `a and b` the first falsy one.
* A def without arguments reads like a value: `$negotiating` or `$negotiating()`.
* `$pending` lists what the agent already did or submitted this turn (`{action, ...args}`): use it in
  param `where` or `when` to stop ordering the same army twice.
* A param `where` may read earlier params: `{"to": {"type": "entity", "of": "province",
  "where": "$linked($params.army.at, $it.id, border)"}}` (the tool then lists every province and
  validation enforces the rule). An enum's `values` may read earlier params the same way
  (`{"to": {"type": "enum", "values": "$params.army.exits"}}`): the tool lists every value they can give.
* Reserved roots cannot be used as local names: $actor $params $it $i $row $inputs $world $physics
  $clock $round $stage $metrics $series $arm $viewer $event $outer $pending $result.
* Contract `defs` are called like built-ins: `$utility($actor, $params.offer)`. A def reads `$records` and
  `$events` as its caller does: in a view or an agent's choices, only what that agent may see.
* Bare words are text even when they match a property name: write `$actor.bet`, not `bet`.
* Strict: unknown props, missing roots and type errors are errors, never silent zeros.
* Ties: `$best` breaks one at random (seeded; `ties: "none"` gives null, `"all"` every tied item). `$sort` puts tied
  items in their order (for a type, creation order) and `$top`, which is `$sort` reversed, the other way round; give
  a list of keys (`[$it.score, $it.age]`) to decide ties yourself.

Roots available by location (plus everywhere: $inputs $world $physics $clock $round $stage
$metrics $series $arm):
ROOTS_TABLE

`$clock` fields: round rounds left unit date label. `$metrics.x` = latest value; `$series.x` = list per round.
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
## Effects (actions.do, events.do)

Assignment text:
* `"$actor.cash -= $params.qty * $params.offer.price"` — also `=`, `+=`, `*=`, `/=`; targets are
  entity props (`$actor.x`, `$params.offer.x`, `$it.x`), `$world.x`, `$physics.x`.
* `"$total = $params.qty * 2"` — a local (`$total`) usable by later effects and the outcome.
* `+=`/`-=` on a list prop append/remove an item; `-=` removes one copy per item (`[1, 2, 2] -= 2` leaves
  `[1, 2]`), comparing like `==`.
* Element assignment: `"$world.board[$i] = $actor.mark"`, `"$actor.scores[round_2] += 1"` (lists and maps).
* Links: `"$link($actor, $params.who, trusts).value += 0.1"`, `"$link($actor, $params.who, trusts).since = $round"`
  (the link must exist; its value keeps to the relation's min/max and fields are typed, like props).
* A write past a numeric prop's, link value's or layer cell's min/max is refused, like a transfer that does not
  fit: an action is rolled back and its actor told why; world logic (an event) that does it fails
  the run at its path. To saturate, say so: `$clamp(x, low, high)`.
  Types are enforced: null too, which only a prop declared with `"default": null` (or no default) may hold.

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
`$best(items, [key1, key2])`: it always gives one item, breaking an exact tie at
random (seeded); add a unique last key when the rule needs a fixed order, or use
`$best(items, key, 'all')` for the list of every item tied for best.
"""

EFFECT_EXAMPLES = {
    "if": '{"if": "$cost > $actor.cash", "then": [...], "else": [...]}',
    "each": '{"each": "offer", "where": "$it.stock == 0", "do": ["$it.listed = false"]}  (with "as": "o", write $o '
            'instead of $it; "sync": true — every item reads the world as it was before the loop and all their '
            'writes land together, for cellular automata and simultaneous updates: only property and layer-cell '
            'assignments, and two items writing different values to one property is an error)',
    "create": '{"create": "review", "count": 1, "name": "Review {$i}", "props": {"stars": "$params.stars"}, '
              '"at": null, "as": "made"}  (in `props`, `$it` is the new entity, so a prop can read an earlier one: '
              '"double": "$it.base * 2"; inside a loop, name the loop\'s item with `as` to read it there)',
    "remove": '{"remove": "$params.target"}',
    "transfer": '{"transfer": "cash", "from": "$actor", "to": "$params.seller", "amount": 10}  (fails the action if '
                'short; in world logic, the run)',
    "link": '{"link": "trusts", "from": "$actor", "to": "$params.who", "value": 0.8, "props": {"since": '
            '"$round"}}  (creates or updates: without `value` an existing link keeps its value and a new one gets '
            'the relation\'s `default`; `props` sets link fields, a new link starting from their defaults)',
    "unlink": '{"unlink": "follows", "from": "$actor", "to": "$params.who"}',
    "move": '{"move": "$actor", "to": "$params.place"}',
    "post": '{"post": "chat", "text": "$params.text", "to": "$params.who", "delay": 2, "drop": 0.1}  (record fields '
            'as keys; to = private recipients; optional `delay` in rounds and `drop` '
            'chance)',
    "emit": '{"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)", "data": '
            '{}, "delay": 1}  (optional `delay` and `drop`, as for post)',
    "fail": '{"fail": "You cannot afford that."}  (roll back the action; text goes to the actor; in world logic it '
            'fails the run)',
    "end": '{"end": "bankrupt", "winner": "$top(player, $it.score, 1)[0]", "say": "..."}',
    "after": '{"after": 3, "do": [...]}  (runs 3 rounds later with the same locals)',
    "wake": '{"wake": "$params.who", "why": "{$actor.name} asked you a question."}  (a turn later; "now": true — '
            'they react as soon as this action has taken effect, before this turn continues, offered the actions '
            'named in "actions": ["accept", "reject"] (without it, every action of the current stage) (a reaction '
            'cannot stop or change the action that woke them: to let others answer first, use a procedure stack; '
            'reactions set off more than 4 deep wait for a normal turn); a wake on a later round goes inside '
            '`after`)',
    "repeat": '{"repeat": "$count(order)", "while": "$count(order) > 1", "do": [...]}  (limit may be an expression; '
              'derive it from the data, not an arbitrary constant; 0 runs nothing; error if still true at the limit)',
    "call": '{"call": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}  (runs a def\'s `do` effects with '
            'those arguments)',
    "chance": '{"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails", "do": '
              '[...]}], "as": "coin"} or {"chance": "deal", "outcomes": "$world.deck", "weight": "1", "as": '
              '"card", "do": [...]}  (picks one outcome from the listed distribution, logged as a `chance` event; '
              '`fg_env.rl.game` can enumerate and choose outcomes instead of sampling them)',
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
"""  # noqa: E501 — guide text: each line is shown as written

RUNNING = """\
## Running (Python)

```python
import fg_env
env = fg_env.load("shop.json", inputs={"budget": 50}, seed=7, arm=None)
print(env.preview("shopper_1"))                   # brief, update, tools, token estimates
result = env.run({"shopper": "policy:thrifty", "owner": my_agent})
result.outputs, result.metrics, result.series, result.stats, result.events, result.summary()
snap = env.snapshot(); env2 = fg_env.Env.restore("shop.json", snap)   # between rounds or stopped mid-round; JSON-safe
exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"]); print(exp.table())
exp.deltas("control")   # paired promo − control per output: mean, sd, ci95, clear (CI excludes 0)
print(fg_env.analysis.report(exp, contract="shop.json", objective="max:profit", require={"fill_rate": ">= 0.95"}))
# plain words for an owner: the choice and its outcome with 80% ranges, drivers, risks, assumptions, fit to data
# (audience="analyst" adds the method; validation=, a run, a sweep or a validation also work; fg-env report file.json)
fg_env.analysis.sweep("shop.json", {"price": {"low": 1, "high": 5, "steps": 5}}, runs=10).table()   # also sensitivity, calibrate, backtest
# every check, experiment and analysis reads data files beside the contract file (or data_dir=) and takes hosts=
v = fg_env.analysis.validate("shop.json", [{"name": "Q1", "inputs": {"start": "2026-01-05"}, "actuals": {"units_by_sku": {...}}}],
                    runs=20, season=4); print(v.report())   # bias, MAPE/WAPE per key, interval coverage, baselines
cal = fg_env.analysis.calibrate("shop.json", cases, {"demand_scale": {"low": 0.5, "high": 2}})   # cases: {name, inputs, targets}
# a rate per case: {"value": 0.03, "count": calls} weighs it by its data; "pool": true matches the cases together
fg_env.analysis.validate("shop.json", cases, uncertainty=cal)   # also experiment, sweep, backtest: draw params per run
fg_env.analysis.behavior_checks("shop.json")   # constant outputs, inputs that change nothing, actions and stages never used
fg_env.rl.tournament("duel.json", {"greedy": "policy:greedy", "llm": my_agent}, games=20).summary()
# seats rotate and share seeds; Elo with intervals, Glicko-2, Nash average, α-Rank, votes, cost per entrant
d = fg_env.analysis.describe("duel.json"); d.markdown, d.metadata   # ODD description; turns, chance, information, players, length

```

A participant is any callable taking a `Wake`:
```python
def my_agent(wake):
    wake.brief, wake.update, wake.tools            # read
    result = wake.call("buy", {"offer": "latte", "qty": 1})   # result.ok, result.text, result.ended
    wake.end()
```
`Wake`: `entity_id name type round stage reason`, `me` (a copy of the agent's own props, private ones too, with `id`
`name` `type` `at`: how a coded participant reads its private value), `brief`, `update` (the text an LLM reads this
turn), `tools` (each a
`ToolSpec`: name, description, input_schema, kind act|look|end, terminal), `tools_for("anthropic"|"openai")`,
`call(name, args)` → `ToolResult(ok, text, ended, data)` (`data.error` is `invalid` or `rejected`),
`end()`, `done`, `calls_left`, `actions_left`. In a simultaneous stage a choice is tried at submit (after the agent's
own earlier choices), so a choice that could not happen is refused immediately and does not use up the turn.
Async participants: an `async def` (or an object with an async `__call__`, or a function that returns an
awaitable) works everywhere, and a simultaneous stage runs them concurrently with the same deterministic
result; so does the built-in LLM participant. A plain function plays one turn at a time (set `concurrent = True` on
a thread-safe one that waits on I/O to run it alongside others). Inside an event loop use `result = await env.arun(participants, ...)`: participants run on that loop,
so clients bound to it work. `wake.time_limit` and `wake.time_left` give the turn's deadline.
`fg_env.load(..., exposures=True)` records what every agent was shown on every wake in `result.exposures`,
`{"texts": {hash: text}, "wakes": [...], "chance": [...]}`: brief, update and view hashes and sizes, news event sequence
numbers, tools offered, every call with its arguments and result, timeouts and undone turns — every text
stored once. `$seen(agent, item)` asks whether an agent was shown an event, a record entry or a view by name;
a contract that uses it records exposures automatically. `experiment`, `tournament`, `evaluate` and `run_jobs`
take `exposures=True` too, every run keeping its own (`--exposures` with `--json`). `result.frames` and
`env.spectate()` give the spectator views (`fg-env run file.json --frames frames.json` saves them).
A big crowd played for many rounds: `fg_env.run(..., events=False)` (or `load`) keeps no event log, so memory stays
flat however long the run: `result.events` is empty (`on_event=` still streams every event), and the run forgets each
event once every agent's news is past it. Everything the run does is the same; a contract that reads `$events` or
`$seen` keeps its log.

Traces: a run with `exposures=True` is a trace (`fg-env run file.json --trace run.jsonl`); `result.save("run.json")`
or `.jsonl`, `fg_env.RunResult.load(path)`. `t = fg_env.analysis.trace(result_or_file)`: `t.overview()` (per agent: turns,
calls, invalid rate, timeouts, tokens), `t.turn(7)` or `t.turn("ana", 3)` (what the agent read, the tools offered,
every call with its result), `t.timeline("ana")`, `t.search("bribe")` (in what agents read or wrote), `t.invalid()`
(refused calls with the correction given), `t.agent("ana")`; each has `.data` and prints as text
(`fg-env trace run.jsonl turn ana 3`). `t.replay("shop.json")` runs the contract again offline with the recorded
calls (`fg_env.participants.replay(t)`) and host answers, and reports the first divergence — a turn, the brief or
update text, the tools offered, a call result, an event or the ending; `fallback="policy:x"` plays on after it
(`fg-env trace run.jsonl replay shop.json`, exit 1 on a divergence). Each wake's `steps` are what it replays.
Outcomes a `chance=` chooser picked are recorded (`exposures.chance`) and replayed without it (a changed chance
node is a divergence); a forked run replays from the snapshot it continued from (`exposures.start`).
Evaluation: `fg_env.rl.evaluate(suite, focal=my_agent, background="policy:reciprocate", seats="villager",
score="$outputs.cash[$seat]", modes={"resident": 0.75, "visitor": 0.25}, runs=20).summary()` runs every scenario and
mode with `focal` in a seeded draw of the seats and again with `baseline` (default: the background) in the same seats
on the same seed: focal score per focal seat, the baseline's, the paired difference with a 95% interval and cost, per
scenario, mode, tag, held out vs in sample and overall. A suite is a contract, a list of scenarios
`{contract, name, inputs, arm, seats, score, background, baseline, modes, tags, held_out}`, or a
`{"scenarios": [...]}` file (`fg-env evaluate suite.json --focal policy:x --mode visitor=0.25`).
Budgets: `env.run(..., budget={"tokens": 200000, "calls": 500, "host_calls": 50, "seconds": 600,
"on_exhaust": "end"})` caps a run: reported input + output tokens, tool calls, host answers on the tape, wall-clock
seconds. It is checked before every round, stage, pass and turn, and `tokens` after every model reply too (the
turn that spends it ends there; other limits let a turn in progress finish): `end` ends the run
(`ended_by: "budget"`), `idle` lets it finish with every agent idle. Either way the run is cut short: it is
degraded (`budget_cut`), not `ok`. `result.budget` has the limits, use and the limit that ran out; snapshots keep it. `experiment` (with `branch_at` the shared rounds count toward each arm),
`tournament`, `evaluate` and `run_jobs` give every run the whole budget, as `--budget tokens=200000` does on
`fg-env run`, `experiment`, `tournament` and `evaluate`. Usage reported after a turn ran out of time still counts.
`env.step(participants)` runs one round; `env.run(participants, rounds=N)` runs N more (an unfinished
run returns provisional outputs). `env.run(..., stop=lambda env: ...)` is checked before every round,
stage, pass and sequential turn; the next `run` continues exactly where it stopped (finishing that
round counts as one of `rounds`). Snapshots are taken between rounds or where a run stopped: one taken
part-way through a round holds the run's last between-round state and every call since, and restoring plays them
back (a long single-round negotiation can be saved turn by turn). A participant that raises fails
the run with its entity id: `fg_env.run` raises the `RunError` (its `.result` is the failed run), `env.run` returns
the run with `status="failed"` and `error`, and experiments keep such runs and carry on.
Read state with `env.entity(id)`, `env.entities(type)`, `env.props`,
`env.result()`, `env.finished`. `env.preview(id)` plays the start of the next round on a copy and shows
exactly the turn the agent will get.

Copies, forks, games and gyms:
```python
with wake.clone() as branch:          # inside a turn: a private copy paused right here (fresh luck; same_luck=True)
    branch.call("buy", {"offer": "latte", "qty": 2}); outcome = branch.run("random")   # the real run never changes
twin = env.clone()                    # between rounds or stopped mid-round: continues exactly like env
what_if = env.fork(arm="promo", patch={...}, effects=["$world.tax = 0.2"])   # between rounds; refuses what cannot follow
game = fg_env.rl.game("kuhn_poker.json"); state = game.new_initial_state()    # OpenSpiel-style
state.current_player(), state.legal_actions(), state.chance_outcomes(), state.child(action), state.returns()
state.information_state(seat), state.observation(seat, "struct"), state.apply_actions({0: a, 1: b})
env = fg_env.rl.gym("nim.json", "a", others="random"); obs, info = env.reset(seed=1)
obs, reward, terminated, truncated, info = env.step({"tool": "take", "args": {"count": 2}})
fg_env.rl.conformance("kuhn_poker.json", sims=20).summary()   # legal calls, chance, clone, serialize, returns, replay, resume, leaks
print(fg_env.rl.playthrough("kuhn_poker.json", seed=1))       # every seat's reading at every decision: a golden text to diff
from fg_env.game.algorithms import CFRSolver, exploitability, minimax, MCTSBot
policy = CFRSolver(game, plus=True).iterate(1000).average_policy(); exploitability(game, policy)
fg_env.run("tic_tac_toe.json", {"x": "mcts:200", "o": "minimax"})   # also "ismcts:200", "cfr:policy.json", "cfr:1000"
aec = fg_env.rl.pettingzoo_aec("kuhn_poker.json", seed=1)     # PettingZoo AEC (reward since last turn); pettingzoo_parallel too
```
Games transform into ordinary contracts: `fg_env.game.repeated(contract, 10)`, `misere`, `zerosum`;
`game.start_at(steps)` starts part-way. Known-answer games live in `examples/contracts/games`.
CLI: `fg-env conformance file.json --sims 50`, `fg-env playthrough file.json --seed 1 [--check golden.txt]`,
`fg-env bench --game file.json`.
A copy is a copy of the run's state, so it is exact (state, random streams, turn numbers, log, recorded host
answers) and costs the same at any depth; turn time limits never run out in a copy. It holds the whole world, hidden state included. Game action ids are fixed
when the game is created (one per combination of listed argument values; free text and lists are
parametric: apply them as `{"tool", "args"}`). `fg_env.load(..., chance=callable)` chooses chance outcomes.

LLM participants: `fg_env.participants.anthropic(anthropic.Anthropic(), "claude-sonnet-5")` or
`fg_env.participants.openai(client, model)` with the sync client, or the string `anthropic:<model>` / `openai:<model>`
(the official client, keyed by `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; `fg-env run --agent`); they loop over tool calls.
Rate limits, timeouts and server errors are retried (`retries=4`), then the turn is forfeited (`forfeits`, and a
`turns_forfeited` diagnostic). Any other error — a rejected API key, an unknown model, a bad request, an async or
unfitting client — fails the run at once with the agent, the provider's error and the fix. A refused reply ends the
turn (`refusals`); openai arguments that are not a JSON object count as invalid calls. Both take `max_tokens` (openai
sends it as `max_completion_tokens`, and also takes `reasoning_effort`) and `extra`, more request fields sent with
every call (`extra={"temperature": 0}`; `{"max_tokens": 1024}` for a server that only knows that field); a reply
cut off at the limit
counts in `truncated` and, when it called no tool, is asked once for a short tool call (`retry_truncated`).
Every truncated reply wastes its whole output: for frequent decisions use `reasoning_effort="low"` (in a Hold'em
evaluation it cut cost by 38% with no visible loss in play), or keep the default effort with a larger `max_tokens`
(6,000 was cut off 9 times in 96 turns).
A reply that still calls no tool after one reminder ends the turn (`no_tool_replies`), and a turn with no action to
take ends without a model call. Retries never wait past the turn's time limit, each request times out with the turn
(at most 10 minutes), and a token budget counts cache writes
in full and cache reads at a tenth; under one, parallel turns wait while the calls under way may spend what is left.
Their real token usage is in `result.stats` (`llm_calls`, `input_tokens` (not read from cache), `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `llm_retries`, `forfeits`, `truncated`, `refusals`, `no_tool_replies`,
and `out_of_steps`: turns that used all `max_steps` model calls); a model seat more than a tenth of whose turns fail
(a turn whose reply the provider refused or cut off counts as failed) degrades the run — passing with `end_turn` where
the stage allows it is a move, not a failure;
your own participants can add theirs with `wake.record_usage(...)`.
Built-ins: `"random"`, `"idle"`, `"policy:<name>"`, and game algorithms `"mcts:N"`, `"ismcts:N"`, `"minimax[:depth]"`, `"cfr:<policy.json|iterations>"`.

`result.events` is the ordered log: `{seq, round, kind, text, actor, to, stage, data}` where kind is
`action` (data: action, params, success), `outcome` (a sealed action's result, to its actor),
`record` (data: record, entry, fields), `news` (event `say`), any `emit` name, or `end` (data: ended_by, winner).
`result.winner` is set by `end` conditions or effects that give `winner`.

CLI: `fg-env check file.json` (static check, then 12 rounds — or up to the last scheduled one-off event — with random agents and with each policy; `--rounds 0` for static only),
`fg-env preview file.json agent_id --rounds 5 --agent trader=policy:quote` (see a mid-run turn),
`fg-env bench [files] --rounds 20` (ms per round, rounds per second and time per phase; no files: the
reference agent-based models), `fg-env check|run|preview|experiment|tournament|evaluate|trace|guide|schema` (`fg-env run file.json --seed 1
--input budget=50 --agent shopper=policy:thrifty --json`).
"""  # noqa: E501 — guide text: each line is shown as written

INSPECT = """\
## Inspecting a run

```python
result = fg_env.run("game.json", seed=1)     # random agents; {"player": my_agent} for yours
print(result.summary())                       # status, winner, outputs, issues, diagnostics, metrics, end state
result.outputs, result.winner, result.ended_by, result.metrics, result.series["price"]
```
Summaries show numbers to 4 decimals; an output's `"format": "money"` (any template format) shows it that way.
Stored values stay exact. A summary ends with each metric's last values and the state the run left: world props and
the first few entities of each type with every prop (`result.state`), so you can look without adding outputs.

`result.diagnostics` is `[{code, path, message, fix}]`: logic problems the run revealed. It reports:
* a tool offered when none of its choices could succeed;
* sealed choices that overwrite each other's values;
* an agent type that never had an action it could take;
* a coded policy rule whose call was refused every time it was tried (`policy_rule_never_acted`), quoting the refusal,
  and a `repeat` policy's rule that was refused after it had acted (`policy_repeat_refused`);
* agents all of whose attempts went wrong, or too many of whose turns failed — more than a tenth for a model
  participant, half for others (`agents_never_acted`, `agents_often_failed`, both degrading), any other failed turns of
  a model participant (or any participant out of time), with their rate (`some_turns_failed`), and turns an LLM
  participant ended out of `max_steps` (`out_of_steps`);
* a stage that can never run, or a measure that reads only what no rule changes;
* host answers that were the contract's fallback stand-ins because no host was bound (`host_fallback`), and a run its
  budget cut short (`budget_cut`) — both degrade the run;
* with model participants, an action that was mostly refused; an action a model or coded policy chose several times
  and was refused every time (`action_never_succeeded`, degrading: what it does, and any mechanism it feeds, never ran
  — a policy's arguments the tool does not accept count as refused calls; random agents' blind calls do not count).

`fg-env check` plays 12 rounds (fewer when the run is shorter; more to reach the last round an event's `when` names,
`$round == 30` or a market's resolution) with random agents and again with each policy on every agent type, and reports
what those plays reveal: crashes as errors (naming the policy that ran into one), diagnostics (including each policy's
always-refused rules) as warnings. Every check plays the same rounds; a time guard stops only a contract too slow to
play, and says so. Before a policy rule acts, the later rules whose action is legal are evaluated too, so a broken rule
is reported even when an earlier one always wins. A population that grows fast enough (agents creating agents) to pass
the engine's ceiling of 1,000,000 living entities before the run ends is a warning: a run fails when it reaches it.
`--rounds 30` plays exactly that many for more evidence.

`result.events` is the log in order: `{seq, round, stage, kind, actor, text, data}`. Its kinds are `action`,
`outcome` (a sealed choice's result), `record`, `news`, `timeout` and `end`. For example:
`[e.get("text") for e in result.events if e["round"] == 3]` (keys without a value are left out).

What agents saw:
* `env.preview("ann")` shows the next turn exactly as ann will get it.
* A recorded run holds every turn: `result = fg_env.run(c, seed=1, exposures=True)`, then `t = fg_env.analysis.trace(result)`.
* `t.overview()` gives turns, calls, invalid rate and tokens per agent.
* `t.turn("ann", 3)` shows what ann read, the tools she was offered, and every call with its result.
* `t.invalid()` lists refused calls with the correction given; `t.search("bribe")` searches the text.

Reproducing: the same seed and participants give the same run. `result.save("run.jsonl")` and
`fg_env.RunResult.load(path)` keep a run. `t.replay("game.json")` runs the contract again with the recorded calls
and names the first divergence.

CLI: `fg-env run game.json --seed 1 --events --trace run.jsonl`, `fg-env trace run.jsonl turn ann 3`.
"""  # noqa: E501 — guide text: each line is shown as written

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
  `result.stats` (invalid_rate and avg_update_tokens should stay low; faulted_actions should be 0) and
  `result.degraded` (empty for a run that shows how the environment plays).
"""
