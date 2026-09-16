# host / tool

### `host.tool`
A host service as an agent tool (web search, retrieval): the `<name>` tool calls the host, returns the result «quoted» and keeps it as evidence in $actor.<name>_evidence (look: <name>_evidence), within per-turn and per-run limits. Results are recorded for replay.

Config:
- `host` (required): Host name of the service (a Tools adapter).
- `who` (required): Agent type(s) that may call it.
- `description` (default ""): Tool description the agent reads.
- `params` (default {"query": {"type": "text", "max_len": 300, "description": "What to look up."}}): Tool parameters, as action params (default: one text `query`).
- `max_calls_per_turn` (default 3): Calls per turn.
- `max_calls_per_run` (default null): Calls per agent over the whole run.
- `max_chars` (default 4000): Longest result kept (longer results are cut).
- `private` (default true): Evidence only the caller sees; false also publishes it to the record <name> at the end of the round.
- `stages` (default null): Stages where the tool is offered (default: every stage whose actions include it).

Actions of the `host` op:
- `call` — takes `args` (needs `args`): {"host": "search", "action": "call", "args": "$params"}  (call the host service for the actor; the result is added to $actor.search_evidence)

```json
{"mechanisms": {"my_tool": {"kind": "host", "mode": "tool", "host": "web_search", "who": "panelist", "max_calls_per_turn": 2, "max_calls_per_run": 6}}}
```
