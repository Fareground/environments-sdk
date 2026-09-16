# events

## `events`: [EventSpec]

What the world does at a set point of a round: at the start or end, on given rounds, every N rounds, when a condition holds, or by chance.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| where/do (with each) | $it $i (or the `as` name) |

**EventSpec** — World logic outside agent turns: scheduled, periodic, conditional or random.
- `name`: text
- `at`: int | [int] | text — Round(s) it fires.
- `every`: int | text — Fires every N rounds, from round 1: a number or an expression over $inputs.
- `when`: text — Fires when true.
- `chance`: any | text — Probability of firing when otherwise due.
- `phase`: text = "start" — start (before stages) | end (after stages).
- `each`: text — Run `do` once per item ($it): a type or expression.
- `as`: text — Name for the item instead of $it.
- `order`: text — With `each`: random (shuffled from the run's seed) or an expression over the item (lowest first); default the order `each` gives.
- `sync`: bool = false — With `each`: every item's rules read the world as it was before the event and all their writes land together (cellular automata, simultaneous updates). Only property and layer-cell assignments are allowed; two items writing different values to one property is an error.
- `where`: text
- `do`: effects
- `say`: text — Headline agents receive as news.
- `once`: bool = false
- `arms`: [text] — Only in these experiment arms.
