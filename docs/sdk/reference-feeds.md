# feeds

## `feeds`: {feed: FeedSpec}

External data written into world props or records, answered by host adapters.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| query/when/fallback | — |

**FeedSpec** — External data written into the world — live or historical prices, news, weather — answered
by a host adapter (``fetch(request)``) at the start of a round, before events and physics.
Every answer is recorded on the host tape, so snapshots, restores and replays never ask again;
text from a host is marked untrusted.
- `host`: text (required) — Name of the host adapter that answers (a Feed).
- `into`: text (required) — 'world.<prop>' (the answer is the new value) or 'records.<record>' (the answer is one entry's fields, or a list of entries).
- `query`: any — What to ask for: data whose texts may be expressions or templates over the world ($world, $clock, $round, $inputs).
- `every`: int = 1 — Fetch every N rounds, from round 1.
- `when`: text — Fetch only when true.
- `fallback`: any — The value (or entries) used when no host is bound: a literal or an expression, whose random draws come from the run's seed. Without one, a run with no host stops and names the host it needs.
- `description`: text
