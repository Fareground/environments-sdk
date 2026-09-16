# host / recap

### `host.recap`
A "story so far" of a long record every N rounds, written by a host writer from the entries since the last recap and posted to the record <name> (delivered as news, recorded for replay).

Config:
- `record` (required): The record to summarise.
- `every` (required): Write a recap every N rounds.
- `host` (default "writer"): Host writer name.
- `model` (default null): Model hint passed to the host.
- `prompt` (default "Summarise the story so far for participants who need to catch up: who did what, what was decided, what is still open. Be faithful and brief."): What the recap asks for.
- `last` (default 50): Most new entries one recap reads.
- `visible` (default "all"): Who reads recaps: 'all' or an expression over $viewer and $it.
- `max_chars` (default 1500): Longest recap kept.
- `fallback` (default null): Without a writer: quote the latest entries (default: stop with an error).

Actions of the `host` op:
- `write`: {"host": "story", "action": "write"}  (recap the new entries of the record now; generated every N rounds)

```json
{"mechanisms": {"my_recap": {"kind": "host", "mode": "recap", "record": "board", "every": 3, "fallback": "extract"}}}
```
