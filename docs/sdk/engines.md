# Engine starters and persona sampling

An **engine** owns reusable interaction mechanics. A **preset** configures those
mechanics for one scenario or game. A custom environment should normally clone
an engine starter and change the smallest relevant inputs, roles and rules.

The behavioral catalog is Market, Council, Dispute, Exchange, Legislature,
Judged Contest, Deliberation, Negotiation, Population, Network, Matching and
Strategy. Parliament and United Nations are Legislature presets. Topic-specific
Commodity/Crypto/Forex/Prediction/Securities/Stock Market starters were retired
in favor of Exchange; Courtroom Trial was retired in favor of Dispute or Judged
Contest. Process Flow and System Dynamics are intentionally excluded because
they are not human-behavior interaction engines.

## Discover and inspect

```python
import fg_env

for engine in fg_env.list_engines(product="arena"):
    print(engine.id, engine.products, engine.status)

market = fg_env.get_engine("market")
print(market.to_dict())
```

`status == "native"` means at least one preset is a native SDK contract.
`legacy_compatible` means the existing Fareground template/module is bundled and
runnable through the SDK compatibility runtime while it is migrated. Deprecated
presets remain labeled for reproducibility and should not start new environments.

## Clone and customize

```python
path = fg_env.clone_engine("market", "campaign_market.json", name="Campaign market")
contract = fg_env.parse(path)
```

Native starters load through `fg_env.load_engine`. Existing game engines load
through the same function and return a legacy `World`:

```python
world = fg_env.load_engine("tic_tac_toe", seed=3, max_rounds=20)
world.run()
```

The compatibility path preserves existing games while they are migrated. Each
engine can move to a native contract without changing its stable catalogue id.
Database-backed builders can use `preset.materialized_source()` to inline a
native starter's bundled imports and tabular input files into one contract.

## Sample people, then assign scenario roles

Persona sampling creates a cohort; an environment still determines what those
people know, want and are allowed to do.

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
version, selection rules, seed, fixed ids and selected ids. Retries reuse the
original run/sample identity; they are not additional observations.

Sampling does not invent missing attributes or prove that a synthetic population
predicts real people. Hosts remain responsible for licensing, privacy,
representativeness and empirical validation.
