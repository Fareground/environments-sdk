# Behavioral engines and persona sampling

An **engine** is a reusable human-interaction mechanism. It is not a finished
environment, topic preset, or Arena game. A custom environment combines one or
more engines with scenario-specific roles, populations, information, rules, and
measurements.

The engine catalog contains exactly these twelve boundaries:

- **Market** — buyers and sellers form demand, supply, prices, and responses.
- **Council** — a bounded group debates an agenda and makes a collective decision.
- **Dispute** — opposing parties present claims and evidence toward a resolution.
- **Exchange** — participants trade configurable assets under configurable rules.
- **Legislature** — legislative bodies use motions, amendments, coalitions, and votes.
- **Judged Contest** — participants submit or perform and human judges choose outcomes.
- **Deliberation** — people exchange reasons, revise views, and seek a conclusion.
- **Negotiation** — parties make proposals, concessions, agreements, or walk away.
- **Population** — sampled people independently respond and outcomes are aggregated.
- **Network** — behavior and information spread through explicit human relationships.
- **Matching** — preferences and eligibility produce selections or pairings.
- **Strategy** — interdependent choices model cooperation, competition, and consequences.

Market, Council, Dispute, and Exchange have native SDK implementations. The
remaining eight are explicit Phase 2 work: they are discoverable so builders can
plan correctly, but cannot be cloned until their reusable mechanics are built.
The SDK does not package existing Fareground environments or Arena games as a
substitute for those missing implementations.

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
Calling `clone_engine` or `load_engine` for a planned engine raises an actionable
`EngineUnavailable` error instead of silently using an environment-specific script.

Database-backed builders can use `engine.materialized_source()` to inline a
native engine's bundled imports and tabular input files into one contract.

## Sample people, then assign scenario roles

Persona sampling creates a cohort; an environment still determines what those
people know, want, and are allowed to do.

```python
sample = fg_env.sample_records(
    population, size=40, seed=11, run=2, resample=True,
    constraints={"state": "IL"}, fixed=[subject_matter_expert],
    group_by="household_id", source="ACS PUMS", source_version="2024 1-year",
)
participants = fg_env.assign_labels(
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
