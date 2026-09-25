# world

## `world`: {prop: default | PropSpec}

Global properties ($world.x).

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
