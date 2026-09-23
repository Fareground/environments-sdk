# core

# fg_env — from a brief to a working environment

An environment is one JSON contract, data not code: what exists (`types`, `entities`), what agents can do
(`actions`), when they act (`stages`), what they see (`views`) and what is measured (`outputs`). Each turn an agent
gets a brief, an update and one typed tool per action it can take right now.

**Faithful first, configurable second.** Implement every requirement and deliverable the brief states, exactly as
stated: its counts and numbers, its rules and timing, who sees what, and every output under the name it gives. Then
make values configurable: an input whose default *is* the brief's value, never a smaller stand-in. Keep every stated
requirement while you repair check issues.

## The loop: write → check → preview → run

1. Write the contract; the example below is a complete one.
2. `fg-env check game.json` (`fg_env.check`): static checks, then short plays with random agents and each policy.
   Fix every error; each names its path, a fix and the guide part that explains it.
3. `fg-env preview game.json <agent id>` (`env.preview(id)`): exactly what that agent reads. Each role should see
   what the brief says it sees, and nothing more.
4. `fg-env run game.json --seed 1` (`fg_env.run`): compare the outputs with what the brief implies, worked out by
   hand for a small case. A clean check proves it runs, not that it is right. To look at state, read the run's summary
   (it ends with metric values and the entities as the run left them) or a preview; never swap outputs for probes.

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
  "types": {"fisher": {"agent": true, "props": {"caught": 0}}},
  "population": [{"type": "fisher", "count": "$inputs.fishers"}],
  "stages": [{"name": "fish", "turns": "simultaneous"}],
  "actions": {"catch": {
    "by": "fisher", "description": "Take fish from the lake this season.",
    "params": {"amount": {"type": "int", "min": 0, "max": "$inputs.max_catch"}},
    "do": ["$got = $min($params.amount, $world.fish)", "$world.fish -= $got", "$actor.caught += $got"],
    "outcome": "You caught {$got} fish."
  }},
  "events": [{"phase": "end", "do": "$world.fish = $min($inputs.capacity, $floor($world.fish * (1 + $inputs.regrowth)))"}],
  "views": {"lake": {"show": "The lake holds {$world.fish} fish. You have caught {caught} in all."}},
  "outputs": {"catch_by_fisher": "$dict(fisher, $it.id, $it.caught)", "fish_left": "$world.fish"},
  "invariants": [{"expr": "$world.fish >= 0 and $world.fish <= $inputs.capacity", "why": "The lake holds 0 to capacity fish."}]
}
```

Each number in the brief is an input defaulting to it; "secretly" is a simultaneous stage; regrowth after the catch
is an end event; outputs carry the brief's names. Save it as `lake.json`; test a case worked out from the brief:

```python
import fg_env

assert fg_env.check("lake.json") == []
print(fg_env.load("lake.json").preview("fisher_1"))

def greedy(wake):  # every fisher asks for 10 every season
    wake.call("catch", {"amount": 10})
    wake.end()

# By hand: 100 → 70 → 84 → 54 → 64 → 34 → 40 → 10 → 12 → 0 fish, so 4 × 30 + 12 = 132 caught.
result = fg_env.run("lake.json", greedy, seed=1)
assert result.ok and result.outputs["fish_left"] == 0
assert sum(result.outputs["catch_by_fisher"].values()) == 132
```

## How a round runs

Start events → each stage in order → end events → metrics → `end` conditions. A run ends when an `end` condition
holds, an `end` effect runs, or the rounds run out.
* A stage wakes agents (`who`, in `order`). `turns: sequential` — one at a time, actions apply at once.
  `turns: simultaneous` — everyone chooses from the same picture, then choices commit together (sealed bids, votes).
* A turn ends after `max_actions` actions (default 1), on `end_turn`, or after `max_calls` calls.
* An action is atomic: if an effect `fail`s or a `transfer` lacks funds, all of it is undone and the agent is told why.

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
| `views` | `{v: {for, title, of, where, sort, desc, limit, show}}` — `of` omitted: one line about `$actor` |
| `events` | `[{phase: start or end, at, every, when, each, do, say}]` |
| `end` | `[{when, winner, say, check: stage or action}]` |
| `metrics`, `outputs` | `{name: expr}` or `{name: {expr, type}}`; an output's `format` (money, pct, 2 …) shapes summaries |
| `invariants` | `[expr or {expr, why}]` — must always hold |
| `patterns` | `{name: {kind, …}}` — trends, seasons, random paths, draws; read `$pattern.name` |
| `mechanisms` | `{name: {kind, mode, ...}}` — markets, auctions, ballots, hidden roles, queues …; `guide('mechanisms')` |
| `policies` | `{name: {rules: [{when, do, with}]}}` — coded participants for baselines (`policy:<name>`) |

Also: `assets`, `game`, `triggers`, `space`, `relations`, `links`, `physics`, `feeds`, `arms`, `calibration`,
`defs`, `blocks`, `imports`. Property types: number int bool text enum list map any (inferred from the default);
inputs also take `table` (rows with `fields`). Parameter types: number int bool text enum entity list file; an
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
* Templates (`show`, `outcome`, `announce`, `say`, `brief`, `name`): `"{name} has {coins} coins"` reads the subject
  (`$it` in lists, `$actor` otherwise); `{$params.amount|money}` is any expression with a format.

## Effects

`do` (actions, events, stage hooks) is one effect or a list:
* `"$actor.coins -= $params.amount"`, `"$total = $params.qty * 2"` (a local); `+=` on a list appends.
* `{"if": "$world.stock < $params.qty", "then": [{"fail": "Not enough stock."}], "else": [...]}`
* `{"each": "player", "where": "$it.coins == 0", "do": ["$it.out = true"]}`
* `{"transfer": "coins", "from": "$actor", "to": "$params.target", "amount": 3}` — fails the action if short
* `{"create": "order", "props": {"price": "$params.price"}}` · `{"remove": "$params.order"}`
* `{"post": "chat", "text": "$params.text"}` · `{"after": 2, "do": [...]}` (later, same locals)
* `{"end": "bankrupt", "winner": "$best(player, $it.coins)", "say": "{$actor.name} went broke."}`

An action's `when` holds requirements: `["$actor.coins > 0", {"expr": "$params.amount <= $world.cap", "why": "Too
much."}]`. Requirements over `$actor` decide whether the tool is offered; ones that read `$params` refuse a call
with their `why`.

## Mechanisms

Ready-made rules that expand into ordinary actions, stages, views and outputs:
`"mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder"}}`.

| kind | modes | for |
|---|---|---|
| `market` | order_book, prediction, auction, posted | Trading venues: continuous order books, auctions and procurement tenders, prediction markets and posted-price shops. |
| `economy` | inventory, ledger, production, supply_chain, demand, replenishment | Money, goods and making things: ledgers (currencies, taxes, loans), inventories, production, supply chains, customers' demand for stocked items and the policies that replenish them. |
| `agreements` | bookings, labor, negotiation, subscriptions | Commitments between agents over time: negotiated deals, jobs, subscriptions and bookings. |
| `decision` | ballot, deliberation | Collective choice: ballots and structured deliberation with motions and votes. |
| `game` | board, cards, pot, slots | Game equipment: boards with enforced rules, cards, betting pots and worker-placement slots. |
| `flow` | procedure, order, victory | Who acts when and how it ends: turn order, procedures with phases, victory conditions. |
| `operations` | queue | Service operations: customers arriving on channels and served by staffed server pools — contact centres, clinics, counters, repair crews — with queues, patience, callbacks and service levels. |
| `groups` | roles, relationships, factions, matching | Who belongs with whom: hidden roles and teams, factions and alliances, relationships, stable matching. |
| `social` | channels, diffusion, feed | Talking and spreading: channels (rooms, direct messages), a social feed, diffusion over a network. |
| `mind` | beliefs, personas, memory | What agents know and remember: beliefs with confidence, memory with recall, generated personas. |
| `conditions` | status, cooldowns, channeling, terrain | Effects on entities over time: statuses, cooldowns, channeled actions and terrain. |
| `host` | judge, game_master, tool, recap | Services the host provides during a run: an LLM judge, a game master, recaps and tools such as web search. |

To have a model do the write → check → preview → run loop for you: `fg-env author brief.md --model anthropic:<model>`
(`fg_env.author`); it keeps the latest contract that checks clean and runs.

## Every other part

`fg_env.guide('<part>')` or `fg-env guide <part>`:
- `authoring` — the start page above with a short reading list: an authoring agent's starting context
- `<section>` — any section above: its fields and the roots available in each
- `model` — how a run works in detail: turns, time limits, hooks, invariants, what an agent reads
- `expressions` — the expression language in full, with every root by location
- `templates` — templates and formats
- `effects` — every effect op with an example
- `functions` — every function by group; `functions.<group>` for one group (e.g. `functions.stats`)
- `mechanisms` — what every family shares; `<family>` and `<family>.<mode>` (e.g. `market.auction`)
- `patterns` — seasons, trends, responses, random processes, draws and noise, and fitting them from data
- `recipes` — data files, continuous time, markets, hidden roles, spaces, networks, physics, feeds
- `macros` — repeat structure from data with `for`/`make`
- `inspect` — debugging a run: summary, diagnostics, events, traces, replay
- `running` — Python API: participants, runs, snapshots, experiments, traces, evaluation, games, gyms, CLI
- `optimise` — the best decision under constraints: objectives, methods, fresh-seed checks, Pareto frontiers
- `checklist` — what makes an environment great for LLM agents

