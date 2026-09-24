# host / memory

### `host.memory`
Per-agent memory: each round what the agent did and read is remembered, plus `note(text)` entries and optional host reflections; importance fades with `half_life`. `recall(query)` returns the most relevant memories (lexical, or host-scored) and strengthens them; a view shows the strongest within `budget` tokens ($memories). Stored in the private property <name> of each agent.

Config:
- `who` (required): Agent type(s) that remember.
- `capture` (default ["did", "saw"]): What is remembered each round: did (own actions), saw (news the agent read).
- `note` (default "note"): Name of the note tool (null: no notes).
- `recall` (default "recall"): Name of the recall tool (null: no recall).
- `stages` (default null): Stages where note and recall are offered (default: every stage whose actions include them).
- `max_chars` (default 500): Longest note, in characters.
- `recall_limit` (default 5): Memories one recall returns.
- `half_life` (default 10.0): Rounds (or clock time) after which recency halves.
- `importance` (default {}): Importance 0–1 per kind (did, saw, note, reflection).
- `weights` (default {}): Recall weights of relevance, recency, importance.
- `limit` (default 200): Memories kept per agent; the faintest are forgotten.
- `budget` (default 300): Tokens of memory shown in the memory view.
- `views` (default true): Show the strongest memories in every update.
- `relevance` (default "lexical"): How recall scores relevance: lexical (no host) or host (a Ranker).
- `host` (default null): Host ranker name (relevance: host).
- `reflect_every` (default null): Write a reflection with a host writer every N rounds.
- `reflect_host` (default "writer"): Host writer for reflections.
- `reflect_prompt` (default "Reflect on these memories: what matters most now, and what should you remember going forward? Answer in two or three sentences."): What a reflection asks for.
- `reflect_fallback` (default null): Without a writer: skip reflections (default: stop with an error).

Actions of the `host` op:
- `note` — takes `text` (needs `text`): {"host": "memory", "action": "note", "text": "$params.text"}  (add a note to the actor's memory)
- `recall` — takes `query` (needs `query`): {"host": "memory", "action": "recall", "query": "$params.query"}  (the actor's most relevant memories as text in $actor.memory_recalled; recalled memories strengthen)

```json
{"mechanisms": {"my_memory": {"kind": "host", "mode": "memory", "who": "panelist", "half_life": 5, "budget": 250}}}
```
