# types

## `types`: {type: TypeSpec}

Kinds of entities and their properties; `agent: true` makes a type act, its `policies` are coded participants for its agents, for crowds and baselines (`policy:<name>`), and its `score` is what each of its agents scores as a seat, for tournaments, game search and gyms.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| inspect | $viewer $it |
| policies.*.rules.* | $actor ($it $i with `each`) |
| score.seat | $it $i |
| score.value | $it (the seat) $result (winner, ended_by) |

**TypeSpec** — A kind of entity. ``agent: true`` types take turns. ``extends`` inherits another type:
its props, its agent flag, its lifecycle hooks and membership (``$count(trader)`` counts
every kind of trader).
- `agent`: bool = false
- `extends`: text — Parent type whose props and role this type inherits.
- `description`: text
- `props`: object
- `owner`: text — The property naming each entity's owner: the id of the agent (or a list of the ids of the agents) that reads its private properties, as an agent reads its own (`"owner": "seller"` on a listing). Without it, only the entity itself and the agent types a property's `private` lists read them.
- `policies`: object — Coded participants for agents of this type (and its subtypes), played as `policy:<name>`: crowds and baselines.
- `policy`: text — The policy agents of this type play when a run names none.
- `score`: ScoreSpec — What each agent of this type scores as a seat, for returns, tournaments, game search and gyms.
- `inspect`: bool | text = false — Whether agents may inspect these entities (each agent may always inspect itself): false (default), true, or an expression over $viewer and $it. Inspect shows every property that is not private.
**PropSpec** — One property. Shorthand: a bare value is the default (``"cash": 100``). An object is always the spec itself, so a
map default is written ``{"default": {"wood": 3}}``.

In a type that ``extends`` another, a property the parent declares is overridden field by
field: only the fields written here change (a bare value changes only the default), so the
parent's ``private``, ``type``, ``min``, ``max`` and ``values`` still apply.
- `type`: text — One of: number, int, bool, text, enum, list, map, any, asset (inferred from default: any number default, 10 as much as 2.5, is a number, which may hold fractions; int only when declared) (`integer`, `float`, `string` and `boolean` are read as int, number, text and bool).
- `default`: any — Literal or expression (evaluated when the entity is created).
- `min`: number — Lowest allowed value: a write below it is refused, never clamped (saturate with $clamp).
- `max`: number — Highest allowed value: a write above it is refused, never clamped (saturate with $clamp).
- `values`: [any]
- `private`: bool | [text] = false — Hidden from every agent but the entity itself and its owner (the agent its type's `owner` property names); the world's are hidden from all. A list of agent types instead of true also shows it to agents of those types (a chair, an auditor). Reading one in what an agent is shown or offered, or in text sent to several, is an error at run time; a refusal whose rules read one spends the action.
- `description`: text
- `unit`: text
**PolicySpec** — A coded participant: the first rule whose condition holds and whose action is legal is taken.
- `rules`: [PolicyRule] (required)
- `repeat`: bool = false — Keep applying rules until the turn ends (default: one action).
**PolicyRule** — One rule of a coded policy: when `when` holds (and the `chance` roll passes), call `do` with `with`.
With `each`, the rule is tried once per item ($it): "for each of my armies, hold".
- `each`: text — A type or expression; the rule is tried for every item ($it).
- `when`: text
- `do`: effects (required) — Action name, or 'pass'.
- `with`: object — Params as values or expressions.
- `chance`: number | text
**ScoreSpec** — What each agent of this type (a seat) scores, for `RunResult.returns`, tournaments, `fg_env.rl.game` and
`fg_env.rl.gym`. The seats are the entities of every type with a score, in creation order unless `seat` orders
them; a reward is the change in a seat's value since its previous step.
- `value`: text (required) — A seat's total score so far: an expression over $it (the seat) and $result, read after every decision and at the end.
- `seat`: text — Seat order: an expression over $it, lowest first (default: the order entities are created). Every scoring type orders seats the same way.
- `utility`: text = "general_sum" — One of: zero_sum, constant_sum, general_sum, identical — the same on every scoring type; zero_sum and identical are checked on every finished run.
- `min`: number — The lowest value a seat can finish with — checked on every finished run.
- `max`: number — The highest value a seat can finish with — checked on every finished run.
