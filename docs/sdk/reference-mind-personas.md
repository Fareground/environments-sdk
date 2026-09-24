# mind / personas

### `mind.personas`
Personas written by a host writer from a prompt template over $it, once per entity before round 1: stored in the `prop` property and the entity's brief, carried by snapshots, recorded for replay.

Config:
- `who` (required): Type whose entities get a persona.
- `prompt` (required): What to write, as a template over $it (the entity and its props).
- `host` (default "personas"): Host writer name.
- `model` (default null): A name for the kind of model wanted (e.g. "strong"), which the host maps to one of its own models (LLMHost(..., models={...})); a host that does not map it uses its own model.
- `prop` (default "persona"): Text property that holds the persona.
- `brief` (default true): Add the persona to the entity's brief.
- `fallback` (default null): Template over $it used when no host is bound (default: stop with an error).
- `max_chars` (default 2000): Longest persona kept (longer text is cut).

Actions of the `mind` op:
- `write`: {"mind": "lives", "action": "write"}  (write any missing personas now; generated for round 1)

```json
{"mechanisms": {"my_personas": {"kind": "mind", "mode": "personas", "who": "shopper", "prompt": "A {age}-year-old shopper with a budget of {budget|money}.", "fallback": "A shopper, age {age}."}}}
```
