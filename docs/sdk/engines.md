# Behavioral engines and persona sampling

An **engine** is a reusable human-interaction mechanism. It is not a finished
environment, topic preset, or Arena game. A custom environment combines one or
more engines with scenario-specific roles, populations, information, rules, and
measurements.

The engine catalog contains exactly these twelve boundaries:

- **Market** — buyers and sellers form demand, supply, prices, and responses.
- **Council** — a panel forecasts, deliberates and forecasts again; final forecasts are scored against a supplied outcome.
- **Dispute** — opposing parties present claims and evidence toward a resolution.
- **Exchange** — participants trade configurable assets under configurable rules.
- **Legislature** — legislative bodies use motions, amendments, coalitions, and votes; floor speeches move stances.
- **Contest** — participants submit and a host judge scores them on a rubric; a rubric tie goes to the stronger hidden performance (skill plus luck).
- **Deliberation** — people exchange reasons, revise views (each speech pulls listeners toward the speaker), and seek a conclusion.
- **Negotiation** — parties make proposals, concessions and agreements, never below their walk-away values.
- **Population** — sampled people independently respond from their inclination, blurred by their uncertainty, and outcomes are aggregated.
- **Network** — behavior and information spread through explicit human relationships.
- **Matching** — both sides rank each other and deferred acceptance makes a stable match within capacity.
- **Strategy** — any number of players' choices play against each other, round-robin, with configurable payoffs and classic coded strategies.

All twelve have native SDK implementations. Legislature, Contest, Deliberation,
Population, Matching and Strategy ship coded baseline participants whose
outcomes vary with the seed, and Network advances mechanically, so a run without
LLM participants is a real simulation rather than a fixed script. Every engine can be discovered,
inspected, cloned, customized, loaded from the installed package, run with a
deterministic seed, supplied with sampled or fixed people, and executed across
an N-run experiment. The SDK packages reusable mechanics and neutral starters;
it does not package finished Fareground environments or named Arena games.
The detailed role, information, action, state, termination, output and extension
contracts for the seven newly generalized engines are in
[Behavioral engine contracts](engine-contracts.md).

Game construction is a separate layer. Generic turn order, simultaneous moves,
hidden information, boards, cards, roles, scoring, victory, and tournament
mechanics remain composable SDK primitives. Named games are configurations of
those primitives, not entries in this behavioral engine catalog.

## Discover, clone, and customize

```python
import fg_env

for engine in fg_env.list_engines():
    print(engine.id, engine.status, engine.available)

path = fg_env.clone_engine("market", "campaign_market.json", name="Campaign market")
contract = fg_env.parse(path)
```

Use `fg_env.list_engines(available=True)` when a builder needs a cloneable engine.
`list_engines(available=True)` returns all twelve engines. The `available` filter
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
