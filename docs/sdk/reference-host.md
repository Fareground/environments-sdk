# host

## Mechanism family `host`

Services the host provides during a run: an LLM judge, a game master, recaps, tools such as web search, agents' memory with recall, and generated personas.

Named the same in every mode:
- `who`: agent type(s) served
- `max_chars`: the longest text accepted or kept
- `private`: keep choices hidden from other agents
- `phase`: start | end: when in the round the mechanism's own step runs
- `views`: generate the mechanism's views

Modes (`"kind": "host", "mode": ...`; read one with `guide('host.<mode>')`):
- `judge`: A rubric judge answered by a host evaluator: the `judge` action (`text` and `subject`) in any effect list (or every new entry of `record`) scores the text per criterion, alone or as a panel, optionally blind.
- `game_master`: Free-text attempts resolved by a host game master: agents get an `attempt(text)` tool; the host proposes effects and the engine applies them only when every one fits `allow` (kinds, targets, properties, bounds, amounts, destinations) — atomically, or refuses.
- `personas`: Personas written by a host writer from a prompt template over $it, once per entity before round 1: stored in the `prop` property and the entity's brief, carried by snapshots, recorded for replay.
- `tool`: A host service as an agent tool (web search, retrieval): the `<name>` tool calls the host, returns the result «quoted» and keeps it as evidence in $actor.<name>_evidence (look: <name>_evidence), within per-turn and per-run limits.
- `memory`: Per-agent memory: each round what the agent did and read is remembered, plus `note(text)` entries and optional host reflections; importance fades with `half_life`.
- `recap`: A "story so far" of a long record every N rounds, written by a host writer from the entries since the last recap and posted to the record <name> (delivered as news, recorded for replay).
- `feed`: External data — live or historical prices, news, weather — answered by a host adapter (`fetch(request)`) at the start of a round, before events and physics, and written into a world property or a record.

Functions:
- `$host_bound(name)` — Whether the host `name` answers this run: bound live, or its answers are on the run's tape (a replay, or a restored run). Branch on it to use a host's judgment only when there is one, e.g. a judge's reading of a speech, and a coded stand-in otherwise.
- `$memories(agent, name, budget?)` — The agent's strongest memories from the memory mechanism `name`, oldest first, within `budget` tokens (default: the mechanism's budget): a list of {id, round, kind, text, label}.
