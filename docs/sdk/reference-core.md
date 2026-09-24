# core

# fg_env guide — the map

Start with `guide('authoring')` (`fg-env guide authoring`): one page with a complete worked contract, the
write → check → preview → run loop and the core language. It is enough for a first environment; read the parts
below only when you need them. To have a model do the loop for you: `fg-env author brief.md --model
anthropic:<model>` (`fg_env.author`); it keeps the best contract that checks without errors and plays soundly.

## Core sections

Every section is optional except `name` and `types`; `guide('<section>')` has its fields and the roots available in
each. The core sections and functions are enough for most environments; the start page teaches them.

| section | what it declares |
|---|---|
| `brief` | Static text every agent reads first: the situation, the rules, and per-type role text. |
| `clock` | How long a run lasts (`rounds`, default 20) and what one round is called. |
| `inputs` | Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables, and the files the environment carries (`type: file`; see guide('assets')). |
| `world` | Global properties ($world.x). |
| `types` | Kinds of entities and their properties; `agent: true` makes a type act, its `policies` are coded participants for its agents, for crowds and baselines (`policy:<name>`), and its `score` is what each of its agents scores as a seat, for tournaments, game search and gyms. |
| `entities` | Named entities (the name defaults to the id), and generated ones: `count` of them, or one per data row (`from`), with sampled traits; ids `<key>_<n>`. |
| `records` | Append-only logs (chat, reviews, bids) with per-viewer visibility; written with `post`. |
| `actions` | What agents can do: each is one typed tool with requirements and atomic effects. |
| `stages` | The steps of every round: who acts, how (sequential or sealed simultaneous), which actions. |
| `views` | What agents read each turn: single lines or ranked, filtered lists. |
| `events` | What the world does at a set point of a round: at the start or end, on given rounds, every N rounds, when a condition holds, or by chance. |
| `end` | Conditions that end the run early, with an optional winner ($result.winner in outputs). |
| `outputs` | The typed results of a run; with `series: true` also sampled every round ($outputs.x latest, $series.x every round). |
| `invariants` | Rules that must always hold. |

Core functions: `$count` `$sum` `$avg` `$min` `$max` `$filter` `$map` `$dict` `$top` `$best` `$any` `$all` `$len` `$get` `$chance` `$randint` `$normal` `$choice` `$round` `$floor` `$clamp` `$entity`; every other function (`guide('functions')`) is extended.

## Extended sections

Reach for one of these when the core cannot say it.

| section | what it declares |
|---|---|
| `triggers` | What the world does the moment a condition becomes true (checked after every action and effect block), unlike an event, which runs at a set point of the round. |
| `mechanisms` | Native building blocks by family (markets, voting, cards, roles …): see guide('mechanisms'). |
| `space` | Positions: a grid, a graph of places or a plane, with values on cells. |
| `relations` | Typed links between entities (trust, follows), with fields, and the links made at build (`links`): listed, from data rows, or generated networks. |
| `physics` | Continuous variables integrated every round (world-level and per entity). |
| `feeds` | External data written into world props or records, answered by host adapters. |
| `arms` | Experiment variants: input overrides or contract patches. |
| `defs` | Reusable expressions, called like built-ins ($utility($actor, 3)), and effect lists (`do`), run with {"call": name, "with": {...}}. |
| `imports` | Contract files merged into this one (relative to it, inside its folder); this contract's own entries win, and imported files may import others. |

## Mechanism, engine or template?

A mechanism (`market`, `decision` …) is a building block inside your contract. An engine (`retail`, `council` …) is a
complete contract to copy and edit: `fg-env new --engine <id>`. A starting template is a small contract to start from:
`fg-env new <template>` with `blank`, `duel`, `shop`, `simulation`, `meeting`.

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

## Engines

Runnable starters, one per kind of human interaction: each a complete contract with coded participants that runs as
cloned, to copy and make your own (topic, roles, people, inputs, rules) rather than start blank:
`fg-env new --engine <id> my_env.json` (`fg_env.engines.clone`); `fg-env engines` lists them.

- `retail` — households choose whether and where to buy while sellers set prices
- `council` — a panel forecasts a yes/no question, deliberates and forecasts again (Brier-scored)
- `dispute` — a civil trial: evidence, objections and cross-examination before a judge, then a jury verdict
- `exchange` — a calibrated trader crowd on a limit order book, with a few trader seats
- `legislature` — members move, second, amend, debate and vote on a measure
- `contest` — participants submit each round and a host judge scores them on a rubric
- `deliberation` — people exchange reasons, propose a conclusion and vote on it
- `negotiation` — two parties trade multi-issue offers and counteroffers before a deadline
- `population` — sampled people each respond to one situation; the responses are aggregated
- `network` — an idea spreads over trust ties from its first adopters
- `matching` — applicants and selectors rank each other; a stable match within capacity
- `strategy` — repeated cooperate-or-compete choices among players under a payoff matrix
- `supply_chain` — tiers of a supply chain order upstream and ship downstream with delays (the beer game)
- `auction` — collectors with private values bid for identical lots under each auction format
- `contact_centre` — a call-by-call day at a contact centre: arrivals, handling, abandonment and staffing
- `ride_hailing` — drivers serve ride requests on a city grid while the platform can price zones by surge
- `epidemic` — an outbreak on a contact network, with lockdowns, vaccination and hospital capacity
- `hidden_roles` — social deduction: hidden werewolves kill at night while the village talks and votes by day

## Every other part

`fg_env.guide('<part>')` or `fg-env guide <part>`:
- `authoring` — the start page: a worked contract, the loop and the core language; read it first
- `model` — how a run works in detail: turns, time limits, hooks, invariants, what an agent reads
- `expressions` — the expression language in full, with every root by location
- `templates` — templates and formats
- `effects` — every effect op with an example
- `functions` — every function by group; `functions.<group>` for one group (e.g. `functions.stats`)
- `mechanisms` — what every family shares; `<family>` and `<family>.<mode>` (e.g. `market.auction`)
- `patterns` — seasons, trends, responses, random processes, draws and noise, and fitting them from data
- `recipes` — data files, continuous time, markets, hidden roles, spaces, networks, physics, feeds
- `assets` — files beside the contract (images, PDFs, text) delivered to agents: `file` inputs
- `inspect` — debugging a run: summary, diagnostics, events, traces, replay
- `running` — Python API: participants, runs, snapshots, experiments, traces, evaluation, games, gyms, CLI
- `optimise` — the best decision under constraints: objectives, methods, fresh-seed checks, Pareto frontiers
- `checklist` — what makes an environment great for LLM agents

