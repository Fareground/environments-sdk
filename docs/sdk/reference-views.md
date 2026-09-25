# views

## `views`: {view: ViewSpec}

What agents read each turn: single lines or ranked, filtered lists.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| when/of | $actor |
| where/sort/show | $actor $it $i |
| with for: spectator | no $actor ($it $i in lists) |

**ViewSpec** — A declared, ranked slice of the world rendered as plain lines for agents.
- `for`: text | [text] = "all" — Agent type(s) that see it, or "spectator": an omniscient view for UIs and reports, rendered into `result.frames` each round and by `env.spectate()`, never shown to an agent.
- `title`: text
- `of`: text — Entity type or expression giving items; omit for a single line.
- `where`: text — Filter ($it, $actor).
- `sort`: text — Sort key ($it).
- `desc`: bool = false
- `limit`: int
- `show`: text (required) — Template for one item (or the single line).
- `empty`: text — Text when no items match (omit to hide the view).
- `when`: text — Show it only when true ($actor, $stage): e.g. "$stage in ['trade']".
- `look`: bool = false — Offer it on demand as look(view) instead of always including it. Randomness a view draws is fixed for the turn: looking again shows the same text.
- `bullet`: bool = true — Prefix each item with '- ' (false for boards and tables).
- `attach`: text — Assets delivered with the view: an expression giving an asset id, a list or null — per listed item ($it) with `of`, else once ($actor).
