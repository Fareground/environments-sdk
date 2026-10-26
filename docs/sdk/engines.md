# Engines

An engine is a complete, larger contract for one kind of human interaction (a market, a trial, a vote …), with coded
participants, that runs as cloned. The kernel ships none: a platform keeps its engines in a folder of its own (a
`manifest.json` listing them, and their starter contracts) and registers it with `fg_env.engines.register(folder)`. Then
copy the closest one and make it yours (its topic, roles, people, information,
rules and measurements). Where a cookbook recipe (`guide('cookbook')`) shows one pattern in a page, an engine is a
worked-out environment. `fg-env engines` lists them; `fg-env new --engine <id> my_env.json` clones one with its
bundled files (`fg_env.engines.clone`). Every engine plays with its coded policies alone, so a run without language
models is already a real simulation, and refuses a setup it cannot honour with the fix.

```python
import fg_env

for engine in fg_env.engines.list_engines(available=True):
    print(engine.id, "—", engine.summary)
path = fg_env.engines.clone("negotiation", "wage_talks.json", name="Wage talks")
print(fg_env.run(path, seed=1).summary())
```

`engine.materialized_source()` inlines an engine's bundled imports and data files into one contract, for a builder
that stores contracts in a database.

**People.** `fg_env.personas` samples a cohort from a population table before a run; the contract still decides what
those people know, want and may do. `sample_records(population, size=40, seed=11, constraints={...}, fixed=[...],
group_by="household_id")` draws with provenance (source, selection rules, seed, ids), `resample=False` repeats a
cohort and a new `run` draws a fresh one; `assign_labels(records, [("consumer", 0.8), ("seller", 0.2)], seed=11)`
gives each person a role. Sampling does not invent missing attributes or prove that a synthetic population predicts
real people.



