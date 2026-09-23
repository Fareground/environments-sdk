# core

# fg_env guide — the map

Start with `guide('authoring')` (`fg-env guide authoring`): one page with a complete worked contract, the
write → check → preview → run loop and the core language. It is enough for a first environment; read the parts
below only when you need them. To have a model do the loop for you: `fg-env author brief.md --model
anthropic:<model>` (`fg_env.author`); it keeps the latest contract that checks clean and runs.

## Sections

Every section is optional except `name` and `types`; `guide('<section>')` has its fields and the roots available in
each.

| section | what it declares |
|---|---|
| `brief` | Static text every agent reads first: the situation, the rules, and per-type role text. |
| `clock` | How long a run lasts (`rounds`, default 20) and what one round is called. |
| `inputs` | Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables. |
| `world` | Global properties ($world.x). |
| `assets` | Files beside the contract — images, PDFs, text, audio — delivered to agents under the visibility rules; see guide('assets'). |
| `types` | Kinds of entities and their properties; `agent: true` makes a type act. |
| `entities` | Named entities (the name defaults to the id). |
| `population` | Generated entities: a count, or one per data row, with sampled traits. |
| `records` | Append-only logs (chat, reviews, bids) with per-viewer visibility; written with `post`. |
| `actions` | What agents can do: each is one typed tool with requirements and atomic effects. |
| `stages` | The steps of every round: who acts, how (sequential or sealed simultaneous), which actions. |
| `views` | What agents read each turn: single lines or ranked, filtered lists. |
| `events` | What the world does at a set point of a round: at the start or end, on given rounds, every N rounds, when a condition holds, or by chance. |
| `triggers` | What the world does the moment a condition becomes true (checked after every action and effect block), unlike an event, which runs at a set point of the round. |
| `end` | Conditions that end the run early, with an optional winner ($result.winner in outputs). |
| `metrics` | Values sampled every round ($metrics.x latest, $series.x every round). |
| `outputs` | The typed results of a run. |
| `invariants` | Rules that must always hold. |
| `mechanisms` | Native building blocks by family (markets, voting, cards, roles …): see guide('mechanisms'). |
| `game` | Seats and what each scores, for tournaments, game search and gyms. |
| `space` | Positions: a grid, a graph of places or a plane, with values on cells. |
| `relations` | Typed links between entities (trust, follows), with fields. |
| `links` | Links made at build: listed, from data rows, or generated networks. |
| `physics` | Continuous variables integrated every round (world-level and per entity). |
| `feeds` | External data written into world props or records, answered by host adapters. |
| `policies` | Coded participants as rules, for crowds and baselines (`policy:<name>`). |
| `arms` | Experiment variants: input overrides or contract patches. |
| `calibration` | Inputs fitted by short pilot sessions every time the contract loads, reproducible from the session's seed; a load that sets a fitted input skips it (each load costs budget × runs pilot sessions). |
| `defs` | Reusable expressions, called like built-ins: $utility($actor, 3). |
| `blocks` | Reusable effect lists, run with {"block": name, "with": {...}}. |
| `imports` | Contract files merged into this one (relative to it, inside its folder); this contract's own entries win, and imported files may import others. |

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
- `macros` — repeat structure from data with `for`/`make`
- `assets` — files beside the contract (images, PDFs, text) delivered to agents
- `inspect` — debugging a run: summary, diagnostics, events, traces, replay
- `running` — Python API: participants, runs, snapshots, experiments, traces, evaluation, games, gyms, CLI
- `optimise` — the best decision under constraints: objectives, methods, fresh-seed checks, Pareto frontiers
- `checklist` — what makes an environment great for LLM agents

