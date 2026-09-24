# types

## `types`: {type: TypeSpec}

Kinds of entities and their properties; `agent: true` makes a type act, its `policies` are coded participants for its agents, for crowds and baselines (`policy:<name>`), and its `score` is what each of its agents scores as a seat, for tournaments, game search and gyms.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| inspect | $viewer $it |
| on_create/on_remove | $it (the entity) + locals |
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
- `policies`: object — Coded participants for agents of this type (and its subtypes), played as `policy:<name>`: crowds and baselines.
- `policy`: text — The policy agents of this type play when a run names none.
- `score`: ScoreSpec — What each agent of this type scores as a seat, for returns, tournaments, game search and gyms.
- `inspect`: bool | text = false — Whether agents may inspect these entities (each agent may always inspect itself): false (default), true, or an expression over $viewer and $it. Inspect shows every property that is not private.
- `on_create`: effects — Effects run for every entity of this type (subtypes too) the moment it is created ($it), atomically with whatever created it; an ancestor's hooks run first.
- `on_remove`: effects — Effects run for every entity of this type (subtypes too) the moment it is removed ($it, already no longer alive), atomically with the removal.
- `on_create_at_build`: bool = true — Also run on_create for entities made when the world is built (once the whole world exists, in creation order); false runs it only for entities created during the run. The nearest declaration in the type's lineage wins.
**PropSpec** — One property. Shorthand: a bare value is the default (``"cash": 100``).

In a type that ``extends`` another, a property the parent declares is overridden field by
field: only the fields written here change (a bare value changes only the default), so the
parent's ``private``, ``type``, ``min``, ``max`` and ``values`` still apply.
- `type`: any — One of: number, int, bool, text, enum, list, map, any, asset (inferred from default) (`integer`, `float`, `string` and `boolean` are read as int, number, text and bool).
- `default`: any — Literal or expression (evaluated when the entity is created).
- `min`: number — Lowest allowed value: a write below it is refused, never clamped (saturate with $clamp).
- `max`: number — Highest allowed value: a write above it is refused, never clamped (saturate with $clamp).
- `values`: [any]
- `private`: bool = false — Hidden from every agent but its owner: an agent owns its own; the world's and any other entity's are hidden from all, except to the reader a view's or entity choice's `where` picks them for (`$it.owner == $actor.id`). Reading one in what an agent is shown or offered, or in text sent to several, is an error at run time; a refusal whose rules read one spends the action.
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
- `chance`: any | text
**ScoreSpec** — What each agent of this type (a seat) scores, for `RunResult.returns`, tournaments, `fg_env.rl.game` and
`fg_env.rl.gym`. The seats are the entities of every type with a score, in creation order unless `seat` orders
them; a reward is the change in a seat's value since its previous step.
- `value`: text (required) — A seat's total score so far: an expression over $it (the seat) and $result, read after every decision and at the end.
- `seat`: text — Seat order: an expression over $it, lowest first (default: the order entities are created). Every scoring type orders seats the same way.
- `utility`: text = "general_sum" — One of: zero_sum, constant_sum, general_sum, identical — the same on every scoring type; zero_sum and identical are checked on every finished run.
- `min`: number — The lowest value a seat can finish with — checked on every finished run.
- `max`: number — The highest value a seat can finish with — checked on every finished run.
