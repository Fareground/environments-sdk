# actions

## `actions`: {action: ActionSpec}

What agents can do: each is one typed tool with requirements and atomic effects. A rule that fails while an action applies (a division by zero, an overflow) refuses and undoes that action alone; the run goes on and its diagnostics name the rule.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| when | $actor ($params too: such a requirement is checked when the action is called) |
| params.*.where | $actor $it $i $params (earlier params) |
| params.*.min/max/values/default | $actor $params (earlier params) |
| do/outcome/announce/terminal | $actor $params + locals |

**ActionSpec** — Something an agent can do. Each legal action becomes one typed tool.
- `by`: text | [text] (required) — Agent type(s) allowed to take it.
- `description`: text — Tool description the agent reads.
- `params`: object
- `when`: [Condition] — Requirements (one or a list). Those over $actor decide whether the tool is offered; those that read $params refuse a call that breaks them, with their `why`. They may not draw at random (nor may parameters' bounds, defaults, values or `where`): a refused call costs nothing, so an agent could call again until luck let it through — draw in `do` or `chance`.
- `do`: effects — Effects applied atomically.
- `outcome`: text — What the actor is told (template over $actor, $params).
- `announce`: text | any — What everyone else is told: omitted, a default line (a simultaneous stage's leaves out the arguments); a template; or false: nobody else learns this action happened.
- `terminal`: bool | text = false — Taking it ends the agent's turn: true, or an expression checked after it applies ($actor, $params).
- `per_turn`: int — Max uses per turn.
- `per_round`: int — Max uses per round.
- `attach`: text — Assets the actor receives with the result (an expression over $actor, $params giving an asset id, a list or null); a sealed choice's arrive with its outcome.
**ParamSpec** — A tool argument. Shorthand: ``"qty": "int"``.
- `type`: text = "number" — One of: number, int, bool, text, enum, entity, list, file (`integer`, `float`, `string` and `boolean` are read as int, number, text and bool)
- `of`: text — Entity type (type entity).
- `where`: text — Which entities qualify ($it, $actor, $params for earlier params, $pending).
- `values`: [any] | text — Allowed values or an expression giving them (type enum).
- `min`: number | text
- `max`: number | text
- `step`: number — Type number or int: values go in steps of this size from `min` (or 0), which makes the parameter enumerable for games.
- `max_len`: int — Maximum length (type text).
- `overflow`: any = "refuse" — Type text: text longer than `max_len` is refused (the agent is told to shorten it), or with `truncate` cut after the last full sentence that fits (the agent is told what was cut).
- `items`: ParamSpec — Type list: the spec every element follows (e.g. {"type": "enum", "values": [...]}). Shorthand: `of` makes entity items, `values` enum items.
- `min_items`: int | text — Type list: fewest elements (a number or an expression, like `min`).
- `max_items`: int | text — Type list: most elements (a number or an expression, like `max`).
- `unique`: bool = true — Type list: no element twice (rankings, hands of cards).
- `default`: any
- `required`: bool — Defaults to true unless a default is given.
- `invalid`: text — What the agent is told when its value is not valid (template over $actor, $params, $value).
- `kinds`: [text] — Type file: the asset types accepted (image, pdf, text, audio, file; default all).
- `max_bytes`: int — Type file: the largest file accepted (default: the largest for its kinds).
- `description`: text
**Condition** — A requirement: ``"$actor.cash > 0"`` or ``{"expr": ..., "why": "You have no money."}``.
- `expr`: text (required)
- `why`: text
