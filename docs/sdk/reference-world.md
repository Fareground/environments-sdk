# world

## `world`: {prop: default | PropSpec}

Global properties ($world.x).

**PropSpec** — One property. Shorthand: a bare value is the default (``"cash": 100``).

In a type that ``extends`` another, a property the parent declares is overridden field by
field: only the fields written here change (a bare value changes only the default), so the
parent's ``private``, ``type``, ``min``, ``max`` and ``values`` still apply.
- `type`: text — One of: number, int, bool, text, enum, list, map, any, asset (inferred from default: any number default, 10 as much as 2.5, is a number, which may hold fractions; int only when declared) (`integer`, `float`, `string` and `boolean` are read as int, number, text and bool).
- `default`: any — Literal or expression (evaluated when the entity is created).
- `min`: number — Lowest allowed value: a write below it is refused, never clamped (saturate with $clamp).
- `max`: number — Highest allowed value: a write above it is refused, never clamped (saturate with $clamp).
- `values`: [any]
- `private`: bool = false — Hidden from every agent but its owner: an agent owns its own; the world's and any other entity's are hidden from all, except to the reader a view's or entity choice's `where` picks them for (`$it.owner == $actor.id`). Reading one in what an agent is shown or offered, or in text sent to several, is an error at run time; a refusal whose rules read one spends the action.
- `description`: text
- `unit`: text
