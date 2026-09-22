# types

## `types`: {type: TypeSpec}

Kinds of entities and their properties; `agent: true` makes a type act.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| inspect | $viewer $it |
| on_create/on_remove | $it (the entity) + locals |

**TypeSpec** — A kind of entity. ``agent: true`` types take turns. ``extends`` inherits another type:
its props, its agent flag, its lifecycle hooks and membership (``$count(trader)`` counts
every kind of trader).
- `agent`: bool = false
- `extends`: text — Parent type whose props and role this type inherits.
- `description`: text
- `props`: object
- `policy`: text — Default coded policy for agents of this type.
- `inspect`: bool | text = true — Whether agents may inspect these entities: true, false, or an expression over $viewer and $it.
- `on_create`: effects — Effects run for every entity of this type (subtypes too) the moment it is created ($it), atomically with whatever created it; an ancestor's hooks run first.
- `on_remove`: effects — Effects run for every entity of this type (subtypes too) the moment it is removed ($it, already no longer alive), atomically with the removal.
- `on_create_at_build`: bool = true — Also run on_create for entities made when the world is built (once the whole world exists, in creation order); false runs it only for entities created during the run. The nearest declaration in the type's lineage wins.
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
