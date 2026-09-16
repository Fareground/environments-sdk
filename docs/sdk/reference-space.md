# space

## `space`: Space

Positions: a grid, a graph of places or a plane, with values on cells.

**Space** — Where entities are (``at``). Declare exactly one of grid, graph, plane.
- `grid`: GridSpace
- `graph`: GraphSpace
- `plane`: PlaneSpace
- `capacity`: int | text | object — Most entities one cell (grid) or place (graph) holds: a number for every entity, or {type: number} (subtypes count). Creating or moving an entity into a full cell is refused. Numbers or expressions over $inputs.
- `layers`: object — {name: LayerSpec}: values stored on every cell (grid) or place (graph).
**GridSpace** — A rows × cols board; positions are [row, col].
- `rows`: int | text (required) — Number of rows (number or expression over $inputs).
- `cols`: int | text (required) — Number of columns (number or expression over $inputs).
- `neighborhood`: text = "von_neumann" — von_neumann (4 neighbours; distance counts steps along rows and columns) | moore (8 neighbours; distance counts king moves) | hex (6 neighbours: a rhombus of hexagons in axial coordinates [r, q], whose neighbours are [r, q±1], [r±1, q], [r-1, q+1] and [r+1, q-1]).
- `torus`: bool = false — The edges wrap around: a position off one side comes back on the other, and distances take the short way.
**GraphSpace** — Named places joined by edges; positions are place names. Distance is the shortest path.
- `nodes`: [text] | text (required) — Place names, or an expression over $inputs giving them.
- `edges`: [any] | text — [a, b] or {from, to, weight}; or an expression over $inputs giving them.
**PlaneSpace** — A width × height area; positions are [x, y]. Distance is straight-line.
- `width`: number | text (required) — Number or expression over $inputs.
- `height`: number | text (required) — Number or expression over $inputs.
- `torus`: bool = false — The edges wrap around (positions and distances, as on a grid).
**LayerSpec** — A value on every cell (grid) or place (graph) without an entity per cell: sugar, pheromone, alive.
Read with ``$layer(name, position)``; changed by the ``layer`` effect.
- `type`: text = "number" — One of: number, int, bool
- `default`: any = 0 — Every cell's starting value: a literal, or an expression over $cell (its position) and $inputs.
- `min`: number
- `max`: number
- `description`: text
