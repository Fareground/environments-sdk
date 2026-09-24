# Behavioral engines and persona sampling

An **engine** is a runnable starter for one kind of human interaction (a market,
a negotiation, a vote …): a complete contract with coded participants that runs
as cloned. Clone the closest one and make it your own: its topic, roles, people,
information, rules and measurements.

The engine catalog contains exactly these eighteen boundaries:

- **Retail** — buyers and sellers form demand, supply, prices, and responses: each household weighs the best café (`inputs.cafes`) against making coffee at home, so total demand falls as prices rise, and coded cafés reprice weekly toward more profit; the sampled households (at least 80) stand for the whole city (weighted by the household table), so café capacities scale with the sample and revenue and turn-aways are reported at city scale.
- **Council** — a panel (`inputs.panel`) forecasts a yes/no question (`inputs.question`, `inputs.briefing`), deliberates and forecasts again; final forecasts are scored against a supplied outcome.
- **Dispute** — opposing parties present claims and evidence toward a resolution; the parties, case, jury and exhibits are inputs (coded counsel lead with their strongest admissible exhibits, so verdicts track the merits, now and then risk a flawed one, and object to any exhibit whose foundation shows a defect); in the jury room each juror's speech pulls the others toward what a bound judge host (`judge`) reads in its words, or without one toward the speaker's leaning; coded awards are a share of `claimed_damages` that grows with a juror's lean, so they scale in proportion to the claim.
- **Exchange** — a calibrated coded crowd trades one instrument on a limit order book while a few trader seats (4 by default) are filled by participants; by default a scripted guidance cut at bar 30 knocks 8% off the fundamental (`inputs.events`, `[]` for none), and `demo_volatility` (per pass) and `bar_stats.sigma` (per bar) report volatility at the two time scales.
- **Legislature** — legislative bodies use motions, amendments, coalitions, and votes; each member's view blends their starting stance with the floor speeches so far, their own party's counting more (`party_loyalty`), so debate moves votes without talking the chamber into unanimity (members may amend; the coded members do not); a speech counts for what a bound judge host (`judge`) reads in its words, and without one for the speaker's own stance.
- **Contest** — at least two participants submit and a host judge scores them on a rubric; a rubric tie goes to the stronger hidden performance (skill plus luck).
- **Deliberation** — people exchange reasons, revise views (each member's view blends their starting stance with the speeches so far), and seek a conclusion; a speech counts for what a bound judge host (`judge`) reads in its words, and without one for the speaker's own stance.
- **Negotiation** — two parties make proposals, concessions and agreements, never below their walk-away values, over at least two rounds; coded parties concede toward the other side as the deadline nears, each at a pace drawn every run (some hold out, some concede early), giving up first the issues they care least about, so they find deals that trade priorities.
- **Population** — sampled people independently respond from their inclination, blurred by their uncertainty (the less confident answer undecided more often), and outcomes are aggregated.
- **Network** — behavior and information spread through explicit human relationships from the initial adopters in `inputs.seeds`; people may also take up or turn down an idea they heard of, or recommend it; adopters, the seed included, stay committed.
- **Matching** — both sides rank each other and deferred acceptance makes a stable match within capacity.
- **Strategy** — any number of players' choices play against each other, round-robin, with configurable payoffs; the classic coded strategies ignore the payoffs by design, and forward-looking players weigh them against the rounds left.
- **Supply chain** — tiers order upstream and ship downstream with delays (the beer game); demand, delays and costs are inputs, and sharing real demand with every tier (`share_demand`) damps the bullwhip effect.
- **Auction** — collectors with private values bid for identical lots, one experiment arm per format (first-price, Vickrey, English, Dutch, uniform-price, double); more collectors raise the price.
- **Contact centre** — a call-by-call day of arrivals, handling and abandonment fitted from a bundled call history; staffing per half-hour is an input, and arms compare a staffing rule, an optimised plan, an outage and callbacks.
- **Ride hailing** — drivers serve requests on a city grid under an hourly demand profile, with optional surge pricing by zone; more drivers cut cancellations.
- **Epidemic** — an outbreak on a contact network with lockdowns, a budget-limited vaccination campaign, hospital capacity and residents who choose whether to comply.
- **Hidden roles** — social deduction among the players (an input): hidden werewolves (their number an input) kill at night while the village talks and votes by day; coded villagers vote at random, so they do not follow what is said.

All eighteen have native SDK implementations, and every one ships coded
baseline participants whose outcomes vary with the seed (Network's baseline
leaves the spread to word of mouth), so a run without LLM participants is a real
simulation rather than a fixed script. Every engine runs at either end of each
declared input's range, and moving a declared numeric input from one end to the other changes an output; a setup an engine cannot honour, such as a three-party
negotiation or a one-player strategy game, is refused with the fix. Every engine can be discovered,
inspected, cloned, customized, loaded from the installed package, run with a
deterministic seed, supplied with sampled or fixed people, and executed across
an N-run experiment.
The detailed role, information, action, state, termination, output and extension
contracts for the seven newly generalized engines are in
[Behavioral engine contracts](engine-contracts.md).

Game construction is a separate layer. Generic turn order, simultaneous moves,
hidden information, boards, cards, roles, scoring, victory, and tournament
mechanics remain composable SDK primitives. Named games are configurations of
those primitives, not entries in this behavioral engine catalog.

## Discover, clone, and customize

From the command line, `fg-env engines` lists every engine with a one-line summary and
`fg-env new --engine <id> my_env.json` clones one (bundled files included). In Python:

```python
import fg_env

for engine in fg_env.engines.list_engines():
    print(engine.id, engine.status, engine.available)

path = fg_env.engines.clone("retail", "campaign_market.json", name="Campaign market")
contract = fg_env.parse(path)
```

Use `fg_env.engines.list_engines(available=True)` when a builder needs a cloneable engine.
`list_engines(available=True)` returns all eighteen engines. The `available` filter
is retained so builders remain compatible with older SDK releases that did not
yet ship every implementation.

## Engine selection

- Use **Population** when people respond independently and the main result is an
  aggregate distribution.
- Use **Network** when explicit human ties, exposure, trust, or influence change
  what people know or do.
- Use **Deliberation** for reason exchange and view revision without a formal
  institutional procedure; use **Council** for a bounded decision-making group.
- Use **Legislature** when motions, seconds, amendments, quorum, thresholds, and
  chamber procedure determine the result.
- Use **Negotiation** for offers and concessions, **Matching** for preferences,
  applications, eligibility, and capacity, and **Strategy** for repeated
  interdependent choices and consequences.
- Use **Contest** when submissions or performances are evaluated by a
  rubric. Keep the generic mechanism simple and configure the actual contest in
  the product environment.

Database-backed builders can use `engine.materialized_source()` to inline a
native engine's bundled imports and tabular input files into one contract.

## Sample people, then assign scenario roles

Persona sampling creates a cohort; an environment still determines what those
people know, want, and are allowed to do.

```python
population = [{"id": f"p{i}", "state": ["IL", "WI"][i % 2], "household_id": f"h{i // 3}"} for i in range(300)]
subject_matter_expert = {"id": "expert", "state": "IL"}
sample = fg_env.personas.sample_records(
    population, size=40, seed=11, run=2, resample=True,
    constraints={"state": "IL"}, fixed=[subject_matter_expert],
    group_by="household_id", source="ACS PUMS", source_version="2024 1-year",
)
participants = fg_env.personas.assign_labels(
    sample.records(), [("consumer", 0.8), ("seller", 0.2)], seed=11,
)
```

Use `resample=False` to repeat runs with an identical cohort. Use a new `run`
number with `resample=True` for a fresh cohort. Provenance records the source,
version, selection rules, seed, fixed ids, and selected ids. Retries reuse the
original run/sample identity; they are not additional observations.

Sampling does not invent missing attributes or prove that a synthetic population
predicts real people. Hosts remain responsible for licensing, privacy,
representativeness, and empirical validation.
