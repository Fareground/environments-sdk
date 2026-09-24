# mechanisms

## Mechanisms (native building blocks)

Declare `"mechanisms": {name: {"kind": <family>, "mode": <mode>, ...config}}`. Each expands into
ordinary actions, stages, world props and events you can read, preview and override (declare the same
name yourself to replace a generated part, a named event or end entry included, but not a world
property: that is the mechanism's state; two mechanisms generating one name is an error). A declared stage that offers only mechanisms' actions and sets no
`max_actions` gives each attached mechanism the actions per turn it has in its own stage. Combine
them freely, several of one mode included: a function reading a mechanism takes its name as the last
argument (`$decisions('committee')`), optional while the contract has only one of that mode. Only a
mechanism made to end the run does (a board's game over, a terminal phase, a deliberation with
`end`): `fg-env check` lists what each one generated, and which can end the run, and
`fg-env expand file.json --mechanisms` shows all of it. A family has one effect op:
`{"<family>": "<mechanism name>", "action": "<action>", ...}`. Read a family with
`guide("<family>")` and one mode with `guide("<family>.<mode>")`.

| kind | modes | for |
|---|---|---|
| `market` | order_book, prediction, auction, posted | Trading venues: continuous order books, auctions and procurement tenders, prediction markets and posted-price shops. |
| `economy` | inventory, ledger, production, supply_chain, demand, replenishment, queue | Money, goods, making things and serving customers: ledgers (currencies, taxes, loans), inventories, production, supply chains, customers' demand for stocked items and the policies that replenish them, and service queues (customers arriving on channels, served by staffed server pools). |
| `agreements` | bookings, negotiation, subscriptions | Commitments between agents over time: negotiated deals, subscriptions and bookings. |
| `decision` | ballot, deliberation, procedure | Collective choice: ballots, structured deliberation with motions and votes, and procedures with phases and objections. |
| `game` | board, cards, pot, status | Game equipment: boards with enforced rules, cards, betting pots, and statuses on pieces (timed conditions that tick, modify properties and block actions). |
| `groups` | roles, matching | Who belongs with whom: hidden roles and teams, stable matching. |
| `social` | diffusion, feed | Spreading: a social feed, and diffusion over a network. |
| `host` | judge, game_master, personas, tool, memory, recap, feed | Services the host provides during a run: an LLM judge, a game master, recaps, tools such as web search, agents' memory with recall, and generated personas. |
| `dynamics` | ode | Continuous change integrated every round, before agents act: world variables and entity properties that follow differential equations (RK4, with Euler–Maruyama noise), read as $physics.<name>. |
| `pattern` | product, sum, trend, seasonal, calendar, cycle, lifecycle, step, series, draw, segments, diffusion, elasticity, cross_price, saturation, threshold, learning_curve, network, hazard, carryover, promotion, reference_price, habit, counts, measurement, censored, missing, random_walk, mean_reversion, autoregressive, volatility, regimes, shocks, noise, weather | Named patterns of the world, read as $pattern.<name>: trends, seasons, responses, random processes, draws, observation noise and memory; `guide('patterns')` teaches them. |

Every family names these the same way:
- `who`: the agent type(s) taking part (a type name or a list; subtypes included)
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `when`: only while this expression is true
- `views`: generate the mechanism's views
- `private`: keep choices hidden from other agents
- `ties`: how a tie is decided: random (from the run's seed) | first (declared order) | none (nobody wins) | share
- `phase`: start | end: when in the round the mechanism's own step runs
- `max_chars`: the longest text accepted or kept
- `currency`: the property (or ledger currency) holding money
- `qty`: a number of units (items, shares, cards, batches); money is `amount`
