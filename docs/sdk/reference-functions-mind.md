# functions / mind

## Functions: mind

- `$belief(agent, key, mechanism?)` — The agent's belief about key: {value, confidence, source, told_by, round}, or null.
- `$beliefs_of(agent, mechanism?)` — The agent's beliefs, most confident first: [{key, value, confidence, source, told_by, round}].
- `$believes(agent, key, value?, mechanism?)` — True when the agent holds a belief about key (and, given a value, believes exactly that) (beliefs mechanism).
- `$confidence(agent, key, mechanism?)` — How sure the agent is about key (0 when it holds no belief).
- `$memories(agent, name, budget?)` — The agent's strongest memories from the memory mechanism `name`, oldest first, within `budget` tokens (default: the mechanism's budget): a list of {id, round, kind, text, label}.
