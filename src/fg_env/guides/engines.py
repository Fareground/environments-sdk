"""``guide('engines')``: every engine in the installed catalog, read from its manifest entry and its starter contract
(roles, coded policies, inputs), so the page cannot drift from what ``fg-env new --engine`` clones."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from ..engines import EngineSpec, list_engines

__all__ = ["engines_page"]

_INTRO = """\
# Engines

An engine is a complete, larger contract for one kind of human interaction (a market, a trial, a vote …), with coded
participants, that runs as cloned: copy the closest one and make it yours (its topic, roles, people, information,
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
"""


def engines_page() -> str:
    """The introduction, then one section per engine."""
    return _INTRO + "\n" + "\n\n".join(_engine(engine) for engine in list_engines(available=True)) + "\n"


def _engine(engine: EngineSpec) -> str:
    source = _expanded(engine)
    types: dict[str, Any] = source.get("types", {})
    roles = [f"`{name}`" + (f" (policies: {', '.join(f'`{p}`' for p in spec['policies'])})"
                            if spec.get("policies") else "")
             for name, spec in types.items() if isinstance(spec, dict) and _acts(types, name)]
    inputs = ", ".join(f"`{name}`" for name in source.get("inputs", {}))
    lines = [f"## `{engine.id}` — {engine.title}", "", engine.description[:1].upper() + engine.description[1:], "",
             f"Clone: `fg-env new --engine {engine.id} my_env.json`. Roles: {', '.join(roles) or 'none (no agents)'}."]
    if inputs:
        lines.append(f"Inputs: {inputs}.")
    return "\n".join(lines)


def _expanded(engine: EngineSpec) -> dict[str, Any]:
    """The engine's starter with its imports merged, as the engine reads it."""
    from ..api import expand

    assert engine.path is not None  # only available engines are listed
    return expand(Path(str(files("fg_env.engines").joinpath(engine.path))))


def _acts(types: dict[str, Any], name: str) -> bool:
    """Whether agents of the type take turns (the flag may come from a type it extends)."""
    seen: set[str] = set()
    while name in types and name not in seen:
        seen.add(name)
        spec = types[name]
        if spec.get("agent"):
            return True
        name = spec.get("extends") or ""
    return False
