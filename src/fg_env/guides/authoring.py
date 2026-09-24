"""The start page for human and agent authors: ``guide('authoring')`` is :data:`START` plus :data:`READ_NEXT`.
``guide()`` is the map of every other part, so nothing here is repeated there."""

START = '''\
# fg_env — from a brief to a working environment

An environment is one JSON contract, data not code: what exists (`types`, `entities`), what agents can do
(`actions`), when they act (`stages`), what they see (`views`) and what is measured (`outputs`). Each turn an agent
gets a brief, an update and one typed tool per action it can take right now.

**Faithful first, configurable second.** Implement every requirement and deliverable the brief states, exactly as
stated: its counts and numbers, its rules and timing, who sees what, and every output under the name it gives. Then
make values configurable: an input whose default *is* the brief's value, never a smaller stand-in. Keep every stated
requirement while repairing check issues.

## The loop: write → check → preview → run

1. Write the contract; the example below is a complete one.
2. `fg-env check lake.json` (`fg_env.check`): static checks, then short plays with random agents and each policy.
   Fix every error; each names its path, a fix and the guide part to read.
3. `fg-env preview lake.json fisher_1` (`env.preview(id)`; a population's ids are `fisher_1`, `fisher_2` …):
   exactly what that agent reads. Each role should see what the brief says, nothing more.
4. `fg-env run lake.json --seed 1` (`fg_env.run`): compare the outputs with what the brief implies, worked out by
   hand for a small case. A clean check proves it runs, not that it is right. To look at state, read the run's summary
   (metric values and the entities as the run left them) or a preview; never swap outputs for probes.

## Worked example

Brief: *Three fishers share a lake of 100 fish for five seasons. Each season every fisher secretly decides how many
fish to catch, 0 to 10. After the catch the lake regrows by 20%, never above 100. Fishers see the lake and their own
total catch. Report `catch_by_fisher` (fisher id → total catch) and `fish_left`.*

```json
{
  "name": "Shared lake",
  "brief": {"rules": "You share a lake. Each season everyone secretly picks a catch; then the lake regrows by {$inputs.regrowth|pct}, up to {$inputs.capacity} fish."},
  "clock": {"rounds": "$inputs.seasons", "unit": "season"},
  "inputs": {
    "fishers": {"type": "int", "default": 3, "min": 1},
    "seasons": {"type": "int", "default": 5, "min": 1},
    "capacity": {"type": "int", "default": 100, "min": 0},
    "max_catch": {"type": "int", "default": 10, "min": 0},
    "regrowth": {"type": "number", "default": 0.2, "min": 0}
  },
  "world": {"fish": "$inputs.capacity"},
  "types": {"fisher": {"agent": true, "props": {"caught": 0, "asked": 0}}},
  "population": [{"type": "fisher", "count": "$inputs.fishers"}],
  "stages": [{"name": "fish", "turns": "simultaneous", "on_exit": [
    "$share = $min(1, $world.fish / $max(1, $sum(fisher, $it.asked)))",
    {"each": "fisher", "do": ["$got = $floor($it.asked * $share)", "$world.fish -= $got", "$it.caught += $got",
                              "$it.asked = 0"]}
  ]}],
  "actions": {"catch": {
    "by": "fisher", "description": "Ask for fish from the lake this season.",
    "params": {"amount": {"type": "int", "min": 0, "max": "$inputs.max_catch"}},
    "do": "$actor.asked = $params.amount", "private": true,
    "outcome": "You asked for {$params.amount} fish."
  }},
  "events": [{"phase": "end", "do": "$world.fish = $min($inputs.capacity, $floor($world.fish * (1 + $inputs.regrowth)))"}],
  "views": {"lake": {"show": "The lake holds {$world.fish} fish. You have caught {caught} in all."}},
  "outputs": {"catch_by_fisher": "$dict(fisher, $it.id, $it.caught)", "fish_left": "$world.fish"},
  "invariants": [{"expr": "$world.fish >= 0 and $world.fish <= $inputs.capacity", "why": "The lake holds 0 to capacity fish."}]
}
```

Each number in the brief is an input defaulting to it; "secretly" is a simultaneous stage whose `on_exit` shares the
fish out once everyone has chosen, so identical choices get identical catches; regrowth after the catch is an end
event; outputs carry the brief's names. Save it as `lake.json`; test a case worked out from the brief:

```python
import fg_env

assert fg_env.check("lake.json") == []
print(fg_env.load("lake.json").preview("fisher_1"))

def greedy(wake):  # every fisher asks for 10 every season
    wake.call("catch", {"amount": 10})
    wake.end()

# By hand: 100 → 70 → 84 → 54 → 64 → 34 → 40 → 10 → 12 → 0 fish; the last 12 are shared: 4 × 10 + 4 = 44 each.
result = fg_env.run("lake.json", greedy, seed=1)
assert result.ok and result.outputs["fish_left"] == 0
assert result.outputs["catch_by_fisher"] == {"fisher_1": 44, "fisher_2": 44, "fisher_3": 44}
```

## How a round runs

Start events → each stage in order → end events → metrics → `end` conditions. A run ends on an `end` condition
or effect, or when rounds run out.
* A stage wakes agents (`who`, in `order`). `turns: sequential` — one at a time, actions apply at once.
  `turns: simultaneous` — everyone chooses from the same picture (sealed bids, votes); choices then commit one
  agent at a time (in `order`, else random), so resolve them jointly in `on_exit`.
* A turn ends after `max_actions` actions (default 1), on `end_turn`, or at `max_calls` calls.
* An action is atomic: if an effect `fail`s or a `transfer` lacks funds, all of it is undone and the agent is told
  why; that costs the action only if it rolled luck or read a hidden value.
* An agent also gets `inspect` (one entity's non-`private` props) when a type sets `"inspect": true` or an
  expression over `$viewer` and `$it`; own state belongs in a view.

## Sections

Every section is optional except `name` and `types`. `guide('<section>')` has each one's fields.

| section | shape and main fields |
|---|---|
| `brief` | `{situation, rules, roles: {type: text}}` — templates |
| `clock` | `{rounds: 20, unit: "round"}` |
| `inputs` | `{name: {type, default, min, max, values, fields}}` — set at load, read as `$inputs.name` |
| `world` | `{prop: default}` — global props, `$world.prop`; a default may read `$inputs` |
| `types` | `{type: {agent, props: {prop: default or {type, default, min, max, values, private}}, extends}}` |
| `entities` | `{id: {type, name, props}}` |
| `population` | `[{type, count, from, name: "Buyer {$i}", props}]` — `from` makes one entity per input row (`$row`) |
| `records` | `{log: {fields, show, visible}}` — logs (chat, bids) written by `post` |
| `actions` | `{act: {by, description, params: {p: {type, min, max, values, of, where}}, when, do, outcome, announce, private}}` |
| `stages` | `[{name, actions, turns, who, order, max_actions, until, on_enter, on_exit}]` |
| `views` | `{v: {for, title, of, where, sort, desc, limit, show}}` — `of` omitted: one line about `$actor`; a list includes the viewer unless `where: "$it.id != $actor.id"` |
| `events` | `[{phase: start or end, at, every, when, each, do, say}]` |
| `end` | `[{when, winner, say, check: stage or action}]` |
| `metrics`, `outputs` | `{name: expr}` or `{name: {expr, type}}`; an output's `format` (money, pct, 2 …) shapes summaries |
| `invariants` | `[expr or {expr, why}]` — must always hold |
| `patterns` | `{name: {kind, …}}` — trends, seasons, random paths, draws; read `$pattern.name` |
| `mechanisms` | `{name: {kind, mode, ...}}` — markets, auctions, ballots, hidden roles, queues …; `guide('mechanisms')` |
| `policies` | `{name: {rules: [{when, do, with}]}}` — coded participants for baselines (`policy:<name>`) |

Also: `assets`, `game`, `triggers`, `space`, `relations`, `links`, `physics`, `feeds`, `arms`, `calibration`,
`defs`, `blocks`, `imports`. Property types: number int bool text enum list map any. Without `type` the default
decides: a number → `number` (fractions too; `"type": "int"` for whole numbers), true/false → `bool`, text → `text`
(`enum` with `values`), a list or object → `list`/`map`, an expression → `any`. Inputs also take `table` (rows with
`fields`). Parameter types: number int bool text enum entity list file; an
`entity` parameter names its type in `of` and may filter with `where` (`$it` the candidate).

## Expressions

A string with `$name` in it is an expression; other strings are text.
* Roots: `$actor` (who acts), `$params` (its arguments), `$it` (the current item), `$world`, `$inputs`, `$round`,
  `$metrics`; locals you assign (`$total`). Props: `$actor.coins`, `$params.target.name`, `$entity(shop).stock`.
  Entities also have `id name type alive`.
* Operators: `+ - * / // % **`, `== != < <= > >=`, `and or not`, `in`, `a if cond else b`, lists `[1, 2]`, maps
  `{price: 3}`, indexing `$list[0]`. Bare words are text: `$actor.role == wolf`. Compare with `==`, never `=`.
* Functions always take `$`: `$count(buyer, $it.cash > 0)`, `$sum(player, $it.coins)`, `$avg`, `$min`, `$max`,
  `$filter(player, $it.alive)`, `$map(player, $it.name)`, `$dict(player, $it.id, $it.coins)`,
  `$top(offer, $it.price, 3)`, `$best(player, $it.score)`, `$any`, `$all`, `$len`, `$get(list, i, 0)`,
  `$chance(0.3)`, `$randint(1, 6)`, `$normal(0, 1)`, `$choice(list)`, `$round(x, 2)`, `$floor`, `$clamp`.
  `$min` `$max` `$sum` `$avg` take a collection and a value (`$min(stand, $it.price)`) or a list; `$min` and
  `$max` also take numbers (`$min(3, $x)`).
* Templates (`show`, `outcome`, `announce`, `say`, `brief`, `name`): `"{name} has {coins} coins"` reads the subject
  (`$it` in lists, `$actor` otherwise); `{$params.amount|money}` is any expression with a format.

## Effects

`do` (actions, events, stage hooks) is one effect or a list:
* `"$actor.coins -= $params.amount"`, `"$total = $params.qty * 2"` (a local); `+=` on a list appends.
  A local lasts to the end of the `do` or hook that sets it (nested `if`/`each`/`after` and `outcome` too), never
  into the next action, event or hook: keep such state in a property.
* `{"if": "$world.stock < $params.qty", "then": [{"fail": "Not enough stock."}], "else": [...]}`
* `{"each": "player", "where": "$it.coins == 0", "do": ["$it.out = true"]}`
* `{"transfer": "coins", "from": "$actor", "to": "$params.target", "amount": 3}` — fails the action if short
* `{"create": "order", "props": {"price": "$params.price"}}` · `{"remove": "$params.order"}`
* `{"post": "chat", "text": "$params.text"}` · `{"after": 2, "do": [...]}` (later, same locals)
* `{"end": "bankrupt", "winner": "$best(player, $it.coins)", "say": "{$actor.name} went broke."}`

An action's `when` holds requirements: `["$actor.coins > 0", {"expr": "$params.amount <= $world.cap", "why": "Too
much."}]`. Requirements over `$actor` decide whether the tool is offered; ones that read `$params` refuse a call
with their `why`.
'''

READ_NEXT = '''
## Read next, only when you need it

`fg_env.guide('<part>')` or `fg-env guide <part>`: a section's fields and roots (`actions`, `stages`, `views`,
`events`, `inputs` …), `effects`, `expressions`, `functions.collections`, `functions.random`, `mechanisms` (markets,
auctions, ballots, hidden roles, queues, inventories), `patterns` (demand, trends and seasons over time), `inspect`
(reading a run). `guide()` maps every part.
'''

AUTHORING = START + READ_NEXT
