# host / feed

### `host.feed`
External data — live or historical prices, news, weather — answered by a host adapter (`fetch(request)`) at the start of a round, before events and physics, and written into a world property or a record. Without a host bound, `fallback` stands in; without one, the run stops and names the host it needs. Text from a host is marked untrusted.

Config:
- `host` (required): Name of the host adapter that answers (a Feed).
- `into` (required): 'world.<prop>' (the answer is the new value) or 'records.<record>' (the answer is one entry's fields, or a list of entries).
- `query` (default null): What to ask for: data whose texts may be expressions or templates over the world ($world, $clock, $round, $inputs).
- `every` (default 1): Fetch every N rounds, from round 1.
- `when` (default null): Fetch only when true.
- `fallback` (default null): The value (or entries) used when no host is bound: a literal or an expression, whose random draws come from the run's seed. Without one, a run with no host stops and names the host it needs.
- `description` (default ""): 

```json
{"world": {"temperature": 10.0}, "clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}, "types": {}, "mechanisms": {"my_feed": {"kind": "host", "mode": "feed", "host": "weather", "into": "world.temperature", "query": {"city": "Millbrook", "date": "{$clock.date}"}, "fallback": "$round($normal(9, 4), 1)"}}}
```
