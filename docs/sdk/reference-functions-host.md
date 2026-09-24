# functions / host

## Functions: host

- `$host_bound(name)` — Whether the host `name` answers this run: bound live, or its answers are on the run's tape (a replay, or a restored run). Branch on it to use a host's judgment only when there is one, e.g. a judge's reading of a speech, and a coded stand-in otherwise.
- `$memories(agent, name, budget?)` — The agent's strongest memories from the memory mechanism `name`, oldest first, within `budget` tokens (default: the mechanism's budget): a list of {id, round, kind, text, label}.
