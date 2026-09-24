# mechanisms

## Mechanisms (native building blocks)

Declare `"mechanisms": {name: {"kind": <family>, "mode": <mode>, ...config}}`. Each expands into
ordinary actions, stages, world props and events you can read, preview and override (declare the same
name yourself to replace a generated part, a named event or end entry included, but not a world
property: that is the mechanism's state; two mechanisms generating one name is an error). A declared stage that offers only mechanisms' actions and sets no
`max_actions` gives each attached mechanism the actions per turn it has in its own stage. Combine
them freely, several of one mode included: a function reading a mechanism takes its name as the last
argument (`$decisions('committee')`), optional while the contract has only one of that mode. Only a
mechanism made to end the run does (victory, a board's game over, a terminal phase, a deliberation with
`end`): `fg-env check` lists what each one generated, and which can end the run, and
`fg-env expand file.json --mechanisms` shows all of it. A family has one effect op:
`{"<family>": "<mechanism name>", "action": "<action>", ...}`. Read a family with
`guide("<family>")` and one mode with `guide("<family>.<mode>")`.

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

Every family names these the same way:
- `who`: the agent type(s) taking part (a type name or a list; subtypes included)
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `when`: only while this expression is true
- `views`: generate the mechanism's views
- `private`: keep choices hidden from other agents
- `ties`: how a tie is decided: random (from the run's seed) | first (declared order) | none (nobody wins) | share
- `phase`: start | end: when in the round the mechanism's own step runs
- `max_chars`: the longest text accepted or kept
- `currency`: the property (or ledger currency) holding money
- `qty`: a number of units (items, shares, cards, batches); money is `amount`
