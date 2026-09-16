# actions

## `actions`: {action: ActionSpec}

What agents can do: each is one typed tool with requirements and atomic effects.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| when | $actor ($params too: such a requirement is checked when the action is called) |
| params.*.where | $actor $it $i $params (earlier params) |
| params.*.min/max/values/default | $actor $params (earlier params) |
| chance/do/otherwise/outcome/announce/terminal | $actor $params + locals |

**ActionSpec** — Something an agent can do. Each legal action becomes one typed tool.
- `by`: text | [text] (required) — Agent type(s) allowed to take it.
- `description`: text — Tool description the agent reads.
- `params`: object
- `when`: [Condition] — Requirements (one or a list). Those over $actor decide whether the tool is offered; those that read $params refuse a call that breaks them, with their `why`.
- `chance`: any | text — Probability of success; `do` on success, `otherwise` on failure.
- `do`: effects — Effects applied atomically.
- `otherwise`: effects — Effects when the chance roll fails.
- `outcome`: text — What the actor is told (template over $actor, $params).
- `announce`: text — What everyone else is told (template).
- `private`: bool = false — Nobody else learns this action happened.
- `terminal`: bool | text = false — Taking it ends the agent's turn: true, or an expression checked after it applies ($actor, $params).
- `per_turn`: int — Max uses per turn.
- `per_round`: int — Max uses per round.
- `duration`: number | text — Continuous clock: how long it takes (number or expression over $actor, $params); the actor's next scheduled turn comes that much later.
- `tool`: text — Offer this action inside one tool of this name, shared by every action naming it: the agent picks the action with the tool's `action` argument, which lists the ones legal now.
- `attach`: text — Assets the actor receives with the result (an expression over $actor, $params giving an asset id, a list or null); a sealed choice's arrive with its outcome.
**ParamSpec** — A tool argument. Shorthand: ``"qty": "int"``.
- `type`: text = "number" — One of: number, int, bool, text, enum, entity, list, file
- `of`: text — Entity type (type entity).
- `where`: text — Which entities qualify ($it, $actor, $params for earlier params, $pending).
- `values`: [any] | text — Allowed values or an expression giving them (type enum).
- `min`: number | text
- `max`: number | text
- `step`: number — Type number or int: values go in steps of this size from `min` (or 0), which makes the parameter enumerable for games.
- `max_len`: int — Maximum length (type text).
- `overflow`: any = "refuse" — Type text: text longer than `max_len` is refused (the agent is told to shorten it), or with `truncate` cut after the last full sentence that fits (the agent is told what was cut).
- `items`: ParamSpec — Type list: the spec every element follows (e.g. {"type": "enum", "values": [...]}). Shorthand: `of` makes entity items, `values` enum items.
- `min_items`: int — Type list: fewest elements.
- `max_items`: int — Type list: most elements.
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
