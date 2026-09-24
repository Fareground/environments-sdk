# expressions

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
| where | extra roots |
|---|---|
| actions.when | $actor ($params too: such a requirement is checked when the action is called) |
| actions.params.*.where | $actor $it $i $params (earlier params) |
| actions.params.*.min/max/values/default | $actor $params (earlier params) |
| actions.do/outcome/announce/terminal | $actor $params + locals |
| stages.who/order | $it $i |
| stages.brief | $actor |
| stages.valid (expr and why) | $actor $pending |
| stages.when/until | — |
| views.when/of | $actor |
| views.where/sort/show | $actor $it $i |
| views.with for: spectator | no $actor ($it $i in lists) |
| records.visible | $viewer $it (entry) |
| records.show | $it (entry: its fields directly, $it.text, plus author, round, seq, stage, to) |
| events.on: round.* / stage.<s>.start / stage.<s>.end / change | — |
| events.on: stage.<s>.turn | $actor $acted $timed_out |
| events.on: create.<t> / remove.<t> | $it (the entity) |
| population.where/weight | $row |
| population.props/id/name | $row $i ($i counts from 1) |
| population.brief | $actor $row $i |
| entities.brief | $actor |
| types.inspect | $viewer $it |
| relations.props.*.default | $from $to |
| links.props | $from $to (+ $row with `rows`) |
| mechanisms.physics.per.*.read/where (dynamics) | $it |
| mechanisms.<feed>.query/when/fallback (host.feed) | — |
| defs.expr | the def's args |
| blocks.do | the block's args + locals |
| policies.rules.* | $actor ($it $i with `each`) |
| metrics.* | — |
| outputs.* | $outputs (earlier outputs) $result (winner, ended_by) |
| end.when/winner/say | — |
| game.seat | $it $i |
| game.returns/rewards | $actor $result (winner, ended_by) |
| invariants.* | — |

`$clock` fields: round rounds left unit date label. `$metrics.x` = latest value; `$series.x` = list per round.

