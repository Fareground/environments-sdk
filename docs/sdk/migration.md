# Migrating a contract to the current form

The contract language became smaller in version 2 (`"fg_env": "2"`): one `events` list for everything that happens
outside turns, one home for each kind of thing, and specialised sections folded into mechanisms. A contract written
for an earlier release still loads — each earlier form is rewritten on load, and `fg_env.check` gives one warning
saying so — but earlier forms stop loading in fg-env 1.0. Save the current form now:

<!-- not run: names your own contract -->
```bash
fg-env migrate my_env.json            # print the current form, every rewrite, and anything left to fix by hand
fg-env migrate my_env.json --write    # save it over the file (imported files are migrated on their own: name them too)
```

In Python, `fg_env.migrate(contract)` returns the current form and a note per rewrite. A file already in the current
form is left exactly as it is.

## What changed

| earlier form | current form |
|---|---|
| `triggers: [{when, do, once}]` | `events: [{"on": "change", when, do, once}]` |
| event `phase: "end"` | `"on": "round.end"` |
| event `at: 5`, `every: 7`, `arms: ["t"]` | a `when`: `"$round == 5"`, `"($round - 1) % 7 == 0"`, `"$arm in ['t']"` |
| event `each`, `where`, `order` | a `do` holding one `each` effect (`"each": "$shuffle(x)"` for random order) |
| stage `on_enter` / `on_exit` | events `"on": "stage.<s>.start"` / `"stage.<s>.end"` |
| stage `on_idle` / `on_timeout` | events `"on": "stage.<s>.turn"` with `"when": "not $acted"` / `"$timed_out"` |
| stage `atomic: true` | `"valid": "true"` |
| stage `time_limit` | the run option `env.run(time_limit=…)` |
| type `on_create` / `on_remove` | events `"on": "create.<type>"` / `"remove.<type>"` |
| action `private: true` | `"announce": false` |
| action `chance` / `otherwise` | an `if` over `$chance(p)` in `do` |
| view `stages: [a]` | `"when": "$stage in ['a']"` |
| `population: [{type, count}]` | a generator entry in `entities`: `{"<type>": {type, count}}` |
| `links: [{relation, …}]` | `relations.<relation>.links` |
| `policies` + `types.<t>.policy` | `types.<t>.policies` (a policy several types play is copied to each) |
| `game: {players, returns, …}` | `types.<player>.score: {value, seat, utility, min, max}` |
| `metrics: {m: e}` | `outputs: {m: {"expr": e, "series": true}}`; `$metrics.m` → `$outputs.m` |
| `blocks` + `{"block": b}` | `defs` with `do` + `{"call": b}` |
| `assets` | inputs of `"type": "file"` |
| `physics`, `patterns`, `feeds` | mechanisms of kind `dynamics`, `pattern`, and `host` mode `feed` |
| `for` / `make` macros | the structure they generate, written out |
| kinds `flow.procedure`, `operations.queue`, `conditions.status`, `mind.memory` | `decision.procedure`, `economy.queue`, `game.status`, `host.memory` |
| `$random()`, `$exists(i)`, `$ids(x)`, `$index_of` | `$uniform(0, 1)`, `$get($entity(i), 'alive', false)`, `$map(x, $it.id)`, `$index` |

## What `migrate` cannot rewrite

These have no mechanical equivalent: `fg_env.check`, loading and `fg-env migrate` report each with its fix.

- **Removed mechanism modes** — `flow.order` (use the stage's `order`), `flow.victory` (`end` entries and a type's
  `score`), `conditions.cooldowns` (a `game.status` status that blocks the action), `conditions.channeling` (a prop
  resolved in a `round.start` event), `conditions.terrain` (space `layers` read with `$layer`), and the unused
  `mind.beliefs`, `social.channels`, `groups.relationships`, `groups.factions`, `game.slots`, `agreements.labor`.
  `examples/contracts/dungeon_skirmish.json` shows every one of these rewritten by hand.
- **Population extras** — `mix`, `quota`, `members`, `raking`: sample and label people before loading with
  `fg_env.personas` (`sample_records`, `assign_labels`), or give each entity its archetype with a prop.
- **Continuous time** — `clock.mode: continuous`, `turns: scheduled`, action `duration`, `wake.in`: environments are
  round-based; make a round the smallest step that matters.
- **`calibration`** — fit inputs before loading with `fg_env.analysis.calibrate` and pass them as `inputs`.

## Names

The distribution is `fg-env`, the import `fg_env`, the command `fg-env`. The template engine (`fg_env.legacy`) was
removed after 0.7; pin `fg-env<0.8` for a project that still runs templates.
