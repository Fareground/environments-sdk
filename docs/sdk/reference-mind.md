# mind

## Mechanism family `mind`

What agents know and remember: beliefs with confidence, memory with recall, generated personas.

Named the same in every mode:
- `who`: agent type whose mind it models
- `phase`: start | end: when in the round the mechanism's own step runs
- `views`: generate the mechanism's views

Modes (`"kind": "mind", "mode": ...`; read one with `guide('mind.<mode>')`):
- `beliefs`: A private world model per agent: beliefs {key: {value, confidence, source, told_by, round}} in the private prop `<name>`, changed by the `learn`, `tell` and `forget` actions, decaying every round.
- `personas`: Personas written by a host writer from a prompt template over $it, once per entity before round 1: stored in the `prop` property and the entity's brief, carried by snapshots, recorded for replay.
- `memory`: Per-agent memory: each round what the agent did and read is remembered, plus `note(text)` entries and optional host reflections; importance fades with `half_life`.

Functions:
- `$belief(agent, key, mechanism?)` — The agent's belief about key: {value, confidence, source, told_by, round}, or null.
- `$beliefs_of(agent, mechanism?)` — The agent's beliefs, most confident first: [{key, value, confidence, source, told_by, round}].
- `$believes(agent, key, value?, mechanism?)` — True when the agent holds a belief about key (and, given a value, believes exactly that) (beliefs mechanism).
- `$confidence(agent, key, mechanism?)` — How sure the agent is about key (0 when it holds no belief).
- `$memories(agent, name, budget?)` — The agent's strongest memories from the memory mechanism `name`, oldest first, within `budget` tokens (default: the mechanism's budget): a list of {id, round, kind, text, label}.
