# events

## `events`: [EventSpec]

What the world does outside agents' turns. `on` is when an event is considered — round.start (the default), round.end, stage.<s>.start, stage.<s>.end, stage.<s>.turn (after each agent's turn: $actor, $acted, $timed_out), create.<type>, remove.<type> ($it), or change (the moment `when` becomes true) — and `when` whether it fires: on given rounds ("$round == 5", "$round % 7 == 1"), in an arm ("$arm == 't'"), by chance. Events on one anchor fire in the order written, before those mechanisms generate.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| on: round.* / stage.<s>.start / stage.<s>.end / change | — |
| on: stage.<s>.turn | $actor $acted $timed_out |
| on: create.<t> / remove.<t> | $it (the entity) |

**EventSpec** — World logic outside agent turns: `on` says when it is considered, `when` whether it fires.
- `name`: text
- `on`: text = "round.start" — round.start (before the stages) | round.end (after them, before outputs are sampled) | stage.<s>.start (when stage s starts) | stage.<s>.end (after it; a simultaneous stage's choices have committed) | stage.<s>.turn (after each agent's turn in it: $actor, $acted, $timed_out) | create.<t> / remove.<t> (inside the change that creates or removes an entity of type t or a subtype: $it) | change (after every change, the moment `when` becomes true; it re-arms once it is false again).
- `when`: text — Fires only when true: "$round == 5", "$round % 7 == 1", "$chance(0.1)", "$arm == 'treatment'".
- `do`: effects — Effects, applied atomically. A `do` that is one `each` loop runs item by item, each item with luck of its own.
- `say`: text — Headline agents receive as news.
- `once`: bool = false — Fire at most once per run.
