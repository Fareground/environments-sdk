# brief

## `brief`: Brief

Static text every agent reads first: the situation, the rules, and per-type role text.

**Brief** — Static text every agent receives once per wake, before anything dynamic (cacheable).
- `situation`: text — What this world is and what is going on (template; {$inputs.x} works).
- `rules`: text — How it works: what agents can do and what happens (template).
- `roles`: object — Extra brief per agent type (template over $actor).
- `attach`: text — Assets every agent receives with its brief: an expression over $actor giving an asset id, a list of them, or null.
