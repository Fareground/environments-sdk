# host

## Mechanism family `host`

Services the host provides during a run: an LLM judge, a game master, recaps and tools such as web search.

Named the same in every mode:
- `who`: agent type(s) served
- `max_chars`: the longest text accepted or kept
- `private`: keep choices hidden from other agents

Modes (`"kind": "host", "mode": ...`; read one with `guide('host.<mode>')`):
- `judge`: A rubric judge answered by a host evaluator: the `judge` action (`text` and `subject`) in any effect list (or every new entry of `record`) scores the text per criterion, alone or as a panel, optionally blind.
- `game_master`: Free-text attempts resolved by a host game master: agents get an `attempt(text)` tool; the host proposes effects and the engine applies them only when every one fits `allow` (kinds, targets, properties, bounds, amounts, destinations) — atomically, or refuses with the reason.
- `tool`: A host service as an agent tool (web search, retrieval): the `<name>` tool calls the host, returns the result «quoted» and keeps it as evidence in $actor.<name>_evidence (look: <name>_evidence), within per-turn and per-run limits.
- `recap`: A "story so far" of a long record every N rounds, written by a host writer from the entries since the last recap and posted to the record <name> (delivered as news, recorded for replay).

Functions:
- `$host_bound(name)` — Whether the host `name` answers this run: bound live, or its answers are on the run's tape (a replay, or a restored run). Branch on it to use a host's judgment only when there is one, e.g. a judge's reading of a speech, and a coded stand-in otherwise.
