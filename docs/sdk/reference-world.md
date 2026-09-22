# world

## `world`: {prop: default | PropSpec}

Global properties ($world.x).

**PropSpec** — One property. Shorthand: a bare value is the default (``"cash": 100``).

In a type that ``extends`` another, a property the parent declares is overridden field by
field: only the fields written here change (a bare value changes only the default), so the
parent's ``private``, ``type``, ``min``, ``max`` and ``values`` still apply.
- `type`: any — One of: number, int, bool, text, enum, list, map, any, asset (inferred from default).
- `default`: any — Literal or expression (evaluated when the entity is created).
- `min`: number — Lowest allowed value: a write below it is refused, never clamped (saturate with $clamp).
- `max`: number — Highest allowed value: a write above it is refused, never clamped (saturate with $clamp).
- `values`: [any]
- `private`: bool = false — Hidden from other agents' inspect tool.
- `description`: text
- `unit`: text
