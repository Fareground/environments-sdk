# functions / space

## Functions: space

- `$at(position, type?)` — Entities at a position (of `type`, subtypes included), in creation order; given an entity, the others at its position.
- `$cells(center?, radius?)` — Every cell of a grid (or place of a graph) with no arguments; with a position or entity, the cells within `radius` (default 1) of it, not its own — on a grid row by row, on a graph nearest first.
- `$chance_for(key, p)` — True with probability p, fixed by `key` (aligned across experiment arms).
- `$clustering(entity, relation)` — Share of an entity's neighbour pairs that are linked to each other (0 to 1).
- `$components(type, relation)` — Groups of entities of `type` connected by `relation`, largest first (lists of entities).
- `$degree(entity, relation)` — How many living entities `entity` is linked with by `relation` (either direction).
- `$distance(a, b)` — Distance between two entities or places in the declared space.
- `$empty(type?)` — Cells (grid) or places (graph) holding no entity (of `type`), in cell order.
- `$hops(a, b, relation)` — Fewest links between a and b along `relation` (either direction); null when unreachable.
- `$layer(name, position)` — The value of a declared layer at a position (or at an entity's position).
- `$link(a, b, kind)` — The `kind` link from a to b — its `value`, `source`, `target`, `kind` and the relation's fields — or null when not linked. Effects assign `.value` or a field: `$link($actor, $it, trusts).since = $round`.
- `$linked(a, b, kind)` — True when a has a `kind` link to b.
- `$links(entity, kind, where?)` — The `kind` links from `entity` (either direction on a symmetric relation) to living entities, each with `value`, `source`, `target` and the relation's fields; `where` filters ($it is a link).
- `$near(center, radius, type?)` — Entities within `radius` of a position or entity (itself left out), in creation order: grid steps by its neighborhood (wrapping on a torus), path length on a graph, straight-line distance on a plane.
- `$nearest(center, type, where?)` — The closest entity of `type` to a position or entity (itself left out) for which `where` holds ($it), or null; candidates are tried nearest first, ties in creation order.
- `$neighbors(entity, kind)` — Entities linked to `entity` by `kind` (either direction).
- `$normal_for(key, mean, sd)` — Normal draw fixed by `key` (aligned across experiment arms).
- `$random_empty(type?)` — One cell holding no entity (of `type`), picked at random; null when every cell is taken.
- `$random_for(key)` — Uniform number in [0, 1) fixed by `key` (an entity, text or list): the same key gives the same draw in every arm of an experiment, however many other draws happen.
- `$relation(a, b, kind)` — Value of the `kind` link from a to b, or null when not linked.
