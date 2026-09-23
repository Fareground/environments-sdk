# Environments SDK contracts

The current contract SDK uses `fg_env`. See the [generated reference](sdk/reference.md) and the installation instructions in the repository README. The distribution is `fg-env` on PyPI.


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
   hand for a small case. A clean check proves it runs, not that it is right.

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
| `events` | `[{phase: start or end, at, every, when, chance, each, do, say}]` |
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
| `market` | order_book, prediction, auction, posted | Trading venues: continuous order books, auctions, prediction markets and posted-price shops. |
| `economy` | inventory, ledger, production, supply_chain, demand, replenishment | Money, goods and making things: ledgers (currencies, taxes, loans), inventories, production, supply chains, customers' demand for stocked items and the policies that replenish them. |
| `agreements` | bookings, labor, negotiation, subscriptions | Commitments between agents over time: negotiated deals, jobs, subscriptions and bookings. |
| `decision` | ballot, deliberation | Collective choice: ballots and structured deliberation with motions and votes. |
| `game` | board, cards, pot, slots | Game equipment: boards with enforced rules, cards, betting pots and worker-placement slots. |
| `flow` | procedure, order, victory | Who acts when and how it ends: turn order, procedures with phases, victory conditions. |
| `operations` | queue | Service operations: customers arriving on channels and served by staffed server pools — contact centres, clinics, counters, repair crews — with queues, patience, callbacks and service levels. |
| `groups` | roles, relationships, factions | Who belongs with whom: hidden roles and teams, factions and alliances, relationships. |
| `social` | channels, diffusion, feed | Talking and spreading: channels (rooms, direct messages), a social feed, diffusion over a network. |
| `mind` | beliefs, personas, memory | What agents know and remember: beliefs with confidence, memory with recall, generated personas. |
| `conditions` | status, cooldowns, channeling, terrain | Effects on entities over time: statuses, cooldowns, channeled actions and terrain. |
| `host` | judge, game_master, tool, recap | Services the host provides during a run: an LLM judge, a game master, recaps and tools such as web search. |

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


---

# Legacy template API reference

The remainder documents `fg_env_kernel`, the older separately published template API. It is retained for existing integrations; new contract authors should use the guide above.

# World template schema

A human-readable reference for the template dict that `Kernel.load()` / `load_world()` accepts. The authoritative sources are the pydantic models in `src/fg_env/pipeline/loader.py` (`WorldTemplate` and its sub-specs) and the machine-readable export in [`kernel_contract.json`](kernel_contract.json) — which also carries the **live registries**: every valid `operation`, `resolution_archetype`, `check_type`, phase handler, and domain module name. When in doubt, that file wins.

All sections are optional (every field has a default), and unknown extra keys are allowed — but a playable world needs at least one `entity_types` entry with `role: "agent"`, one entity of that type, and one action whose `actor_type` matches it.

Expressions: any field named `expr` (and many `value` fields) accept the safe expression grammar — `$actor.gold >= 100 && $count(player, alive) > 1`, with roots `$actor`, `$target`, `$params`, `$state`, `$last_event`, `$result` and functions like `$random(min, max)`, `$dice(n, sides)`, `$lookup(table, key)`. Prefer `expr` strings over the legacy structured operator form.

## Top-level layout

| Key | Type | Purpose |
| --- | --- | --- |
| `name`, `description` | str | Metadata; both surface to agents via `world_brief`. |
| `rules` | str | Natural-language how-to-play brief (markdown). Injected into every agent's perception as `world_brief.rules`. |
| `contract_version` | str | Contract version, default `"1.0"`. Auto-upgraded by `compile_template`. |
| `entity_types`, `resource_types`, `relation_types`, `visibility_rules`, `spatial`, `temporal`, `tables` | — | Schema layer (type definitions). |
| `actions`, `triggers`, `derived_rules`, `termination_conditions`, `domain_modules` | — | Rules layer (behavior). |
| `entities`, `resource_holdings`, `resource_unallocated`, `initial_relations`, `factions`, `adjacency` | — | Instance layer (starting world). |
| `physics`, `property_dynamics` | dict | Continuous dynamics (below). |
| `location_definitions`, `goals`, `skill_definitions`, `initial_skills`, `recipes`, `connectors`, `runtime_parameters` | list | Optional subsystems. |
| `cognitive_config`, `social_config`, `crowd_config` | dict | Subsystem toggles (wire up when `enabled: true`). |
| `viz` | dict | Rendering hints. Opaque to the kernel. |

## entity_types

Type definitions for everything that exists in the world.

```json
{"entity_types": [
  {"name": "Player", "role": "agent", "description": "A player.",
   "properties": [
     {"name": "gold", "type": "int", "default": 100, "min_value": 0},
     {"name": "mark", "type": "string", "default": ""}
   ]}
]}
```

- `role` — `agent` (gets turns) | `object` | `location` | `abstract`.
- Property `type` — `float` (default) | `int` | `string` | `bool` | `enum` | `list`. Extras: `default`, `min_value`, `max_value`, `enum_values`, `description`, `hidden` (excluded from other agents' perception).

## entities

Starting instances. Type defaults fill in any property you don't override.

```json
{"entities": [
  {"id": "player_x", "entity_type": "Player", "name": "Player X",
   "properties": {"mark": "X"}, "location_id": null, "alive": true}
]}
```

Required: `id`, `entity_type`.

## actions

What agents can do. Only `name` is required.

```json
{"actions": [
  {"name": "attack",
   "description": "Strike an adjacent enemy.",
   "actor_type": "Warrior",
   "target_type": "Warrior",
   "parameters": [
     {"name": "power", "type": "int", "min": 1, "max": 10, "description": "Effort."}
   ],
   "preconditions": [
     {"expr": "$actor.stamina >= 2", "description": "Needs stamina."}
   ],
   "resolution_archetype": "skill_check",
   "resolution_params": {"skill_property": "strength"},
   "effects_on_success": [
     {"operation": "subtract", "target": "target", "field": "hp", "value": "$params.power"}
   ],
   "effects_on_failure": [
     {"operation": "subtract", "target": "actor", "field": "stamina", "value": 1}
   ]}
]}
```

Field notes:

- `actor_type` — entity type allowed to perform it; `target_type` — entity type of the (optional) target.
- `parameters` — free-form dicts describing per-call arguments (surfaced to agents; passed as `ActionInstance.parameters` and readable in effects via `$params.<name>`).
- `resolution_archetype` — how success is decided. `deterministic` (default, always succeeds) or any registered archetype: `skill_check`, `contest`, `voting`, `order_book`, `cpmm`, the auction family, etc. — live list in `kernel_contract.json`. `resolution_params` configures it.
- `effects_on_success` / `effects_on_failure` / `effects_on_partial` — effect lists (below).
- Sequencing: `requires_action` (+ `requires_action_success`), `cooldown_rounds`, `sequence_rounds` (multi-round actions), `interruptible`, `unlocks_actions`, `locks_actions`.

  In discrete simulations, `sequence_rounds: N` counts the starting round: effects
  resolve on the Nth round, at most one progress tick per world round even with
  multiple phases. Sequential, simultaneous and parallel execution use the same
  lifecycle. Continuing an action preserves its original target and parameters;
  changing them requires cancelling and starting new work. Passing or choosing a
  different action cancels interruptible work. Non-interruptible work continues
  without another model decision. Runtime restrictions are rechecked before
  progress; invalidated work is cancelled without applying completion effects.
  Checkpoints persist the committed inputs, progress and last advancement round.
- `message_action: true` — the action carries a message payload.
- `broadcast: false` — the resolved action is not announced to every agent (night kills, secret votes).
- `range` — max spatial distance to the target.
- `duration` — sim-time length of the action in continuous mode (see temporal).

### Preconditions

Prefer the expression form; the structured form (`subject`/`operator`/`value`/`field`/`resource`/`relation_type`) is legacy. Unknown operators fail loudly at load.

```json
{"expr": "$actor.gold >= 100 && $actor.alive"}
```

### Effects

```json
{"operation": "transfer_resource", "target": "target", "resource": "gold",
 "value": "$params.amount",
 "condition": {"expr": "$target.alive"},
 "scale_by_magnitude": true}
```

- `operation` (required) — one of the built-in + registered ops (`set`, `add`, `subtract`, `multiply`, `transfer_resource`, `move_to`, `kill`, `spawn_entity`, `emit_event`, `conditional`, `for_each`, `sequence`, board/card/token ops, ...). Live list: `kernel_contract.json → live_capabilities.effect_operations`. Unknown ops fail loudly at load.
- `target` — `actor` | `target` | an entity id | an expression.
- `field` / `value` — property to change and the (possibly `$`-expression) value.
- `resource`, `relation_type` — for resource/relation ops.
- `condition` — optional guard (`expr` preferred); the effect is skipped when false.
- `scale_by_magnitude` — scale numeric value by the resolution result's magnitude.

## relation_types / initial_relations

Typed, directed (or symmetric) weighted edges between entities.

```json
{"relation_types": [
   {"name": "trust", "symmetric": false, "default_value": 0.0,
    "min_value": -1.0, "max_value": 1.0}
 ],
 "initial_relations": [
   {"from": "alice", "to": "bob", "type": "trust", "value": 0.5}
 ]}
```

Edges also accept the `{from_entity, to_entity, relation}` spelling.

## resource_types / holdings

Conserved (or not) quantities held by entities.

```json
{"resource_types": [
   {"name": "gold", "conservation": true, "discrete": true,
    "min_value": 0, "max_value": null, "initial_supply": 1000}
 ],
 "resource_holdings": [
   {"resource": "gold", "entity_id": "alice", "amount": 100}
 ],
 "resource_unallocated": [
   {"resource": "gold", "amount": 900}
 ]}
```

## visibility_rules

Who sees what. Without rules, defaults apply; `hidden` properties are never shown to others.

```json
{"visibility_rules": [
  {"observer_type": "Player",
   "visible_entity_types": ["Player", "Item"],
   "visible_properties": {"Player": ["hp"]},
   "max_range": 5.0,
   "requires_same_location": false,
   "see_relations_involving_self": true,
   "see_all_relations": false,
   "visible_resources": ["gold"]}
]}
```

## spatial

```json
{"spatial": {"type": "grid", "rows": 10, "cols": 10}}
```

- `type: "none"` (default) — no space.
- `type: "grid"` — `rows`/`cols` (or legacy `width`/`height`).
- `type: "graph"` — `locations` (or `nodes`) list + `edges` (`{"from": "a", "to": "b", "weight": 1.0}` or `["a", "b"]`). Edges mirror into adjacency for `$is_adjacent`.
- `type: "continuous_2d"` — `width`/`height` floats.

## temporal

```json
{"temporal": {
  "max_rounds": 50,
  "phases": [
    {"name": "night", "active_roles": ["Werewolf"], "resolution_mode": "sequential"},
    {"name": "day", "handler": "assign_properties", "handler_params": {}}
  ],
  "round_duration_seconds": 86400,
  "sim_start_iso": "2026-01-01T00:00:00",
  "time_unit_label": "day"
}}
```

- `max_rounds` — the round budget. Honored by `load_world` and the facade; without a termination condition it is the only stop. `Kernel.load(max_rounds=...)` overrides it.
- `phases` — ordered per-round phases; default is a single `action` phase. Per-phase: `active_roles`, `initiative_type` (`fixed` | property-driven via `initiative_property` + `initiative_descending`), `handler` + `handler_params` (registered phase handlers — live list in `kernel_contract.json`), `resolution_mode` (`sequential` | simultaneous).
- Time mapping (`round_duration_seconds`, `sim_start_iso`, `time_unit_label`) — optional; gives agents calendar context in perception.
- **Continuous mode** — `"mode": "continuous"` plus a `continuous` block; per-action `duration` fields let slow and fast actions share one clock:

```json
{"temporal": {"mode": "continuous",
  "continuous": {"default_turn_interval": 1.0, "max_time": 100.0,
                 "environment_interval": 1.0, "action_durations": {"negotiate": 48.0}}}}
```

## termination_conditions

When the sim ends. `World.terminated_by` reports the `name` of the condition that fired. Optional but recommended.

```json
{"termination_conditions": [
  {"name": "last_survivor", "check_type": "expr",
   "params": {"expr": "$count(Player, alive) <= 1"}},
  {"name": "either", "check_type": "compound_or", "sub_conditions": [
    {"name": "rich", "check_type": "expr", "params": {"expr": "$state.entities.alice.gold >= 1000"}},
    {"name": "board_win", "check_type": "board_pattern",
     "params": {"board_id": "main", "patterns": ["row_3", "col_3", "diag_3"]}}
  ]}
]}
```

- `check_type` — `expr` (preferred) or any registered check: `last_one_standing`, `first_to_score`, `faction_win`, `resource_exhausted`, `round_limit`, `compound_and`/`compound_or` (with `sub_conditions`), etc. Live list: `kernel_contract.json → live_capabilities.termination_check_types`.
- An `expr` in `params` alongside another `check_type` acts as an AND-guard.

## physics

Continuous coupled dynamics, integrated with RK4 between turns. Deterministic and serializable.

```json
{"physics": {
  "params": {"alpha": 1.1, "beta": 0.4, "delta": 0.1, "gamma": 0.4},
  "variables": [
    {"name": "prey", "value": 10, "rate": "alpha*prey - beta*prey*pred", "min": 0},
    {"name": "pred", "value": 5,  "rate": "delta*prey*pred - gamma*pred", "min": 0}
  ]
}}
```

Variables can read entity aggregates and write values back onto entities — see `src/fg_env/physics.py` (`EntitySource`, `EntityWriteback`) for those blocks.

### property_dynamics

Autonomous per-entity drift/spawn/cascade rules, ticked every round: `{"property_dynamics": {"drift_rules": [...], "spawn_rules": [...], "cascade_rules": [...]}}` — see `src/fg_env/property_dynamics.py` for rule shapes.

## domain_modules

Pre-registered Python modules referenced by name — the template never contains Python. Each takes free-form `params`.

```json
{"domain_modules": [
  {"name": "board", "params": {"id": "main", "kind": "grid", "rows": 3, "cols": 3,
                               "auto_marks": ["X", "O"]}}
]}
```

Available names (chess, poker, monopoly, mafia, prediction_market, securities_trading, turn_manager, ...): `kernel_contract.json → live_capabilities.domain_modules`. Modules contribute perception (`domain_data`), effects, phase handlers, and can narrow the valid-action list.

## Everything else

- `tables` — named lookup tables for `$lookup(table, key)`.
- `runtime_parameters` / `last_runtime_params` — named tunables exposed under the `runtime` table.
- `triggers`, `derived_rules` — reactive rules and the inference layer; free-form dicts consumed by their engines.
- `factions` — `{id, name, member_ids, parent, properties}` teams.
- `adjacency` — `{location, neighbors}` edges when not using a graph space.
- `location_definitions` — per-location properties, entry requirements, modifiers, and tick effects.
- `goals`, `skill_definitions` + `initial_skills`, `recipes` (crafting), `connectors` — optional subsystems; see their modules under `src/fg_env/`.

### Action input validation

Declared action parameters are validated before messages or state changes are
applied. A missing `required` parameter without an explicit default rejects the
whole action. Invalid numbers (including NaN/infinity), invalid declared types
and values outside declared enum choices also reject it. Numeric strings may be
coerced, numeric bounds clamp values, and explicit defaults are validated through
the same contract. The event log records `action_failed` with
`reason: "invalid_parameters"` and field errors; valid corrections emit
`action_corrected`.

Built-in `smoke_test` strategies use seeded sample values and typed targets to
exercise parameterized mechanics. These samples test structure; they are not
participant judgments. A callable `decisions` argument is used unchanged, so it
can test specific valid or invalid inputs. Integrations implementing a baseline
policy can reuse `fg_env.policies.sample_action_parameter`.
