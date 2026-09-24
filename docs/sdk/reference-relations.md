# relations

## `relations`: {relation: RelationSpec}

Typed links between entities (trust, follows), with fields, and the links made at build (`links`): listed, from data rows, or generated networks.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| props.*.default | $from $to |
| links.*.props | $from $to (+ $row with `rows`) |

**RelationSpec** — A kind of link between entities (follows, trusts, owns …). Every link carries a number
(``value``) and, with ``props``, typed fields of its own (``since``, ``channel``, ``strength``).
- `symmetric`: bool = false
- `default`: number — Value of a link made without one (default 1).
- `min`: number — Lowest allowed link value: a write below it is refused, never clamped (saturate with $clamp).
- `max`: number — Highest allowed link value: a write above it is refused, never clamped (saturate with $clamp).
- `props`: object — Typed fields every link carries, read as $link(a, b, kind).field; defaults may be expressions over $from and $to.
- `links`: [LinkSpec] — Links made when the world is built, after the entities.
- `description`: text
**LinkSpec** — Starting links of a relation: one explicit link, links from data rows, or a generated network among a
type.
- `from`: text
- `to`: text
- `value`: any = 1
- `among`: text — Generate links among entities of this type.
- `graph`: text — complete | ring | random | small_world | scale_free | blocks | lattice | star | bipartite
- `m`: int | text — scale_free: links each new member makes (preferential attachment).
- `block`: text — blocks: expression over $it giving each member's group; `p` applies within a group, `p_between` across.
- `p_between`: number | text — blocks: link probability between groups.
- `with`: text — bipartite: the other type (links run among → with).
- `hub`: text — star: expression giving the hub entity (default: the first member).
- `rows`: text — Edges from data: an expression giving rows with `from`, `to` and optional `value`.
- `degree`: int | text — Mean neighbours per member (number or expression); on a one-way random graph a link either way makes a neighbour.
- `p`: number | text — Link probability (random) or rewiring probability (small_world). For random it may depend on the pair: '0.1 if $to.influencer else 0.02'.
- `props`: object — Link field values or expressions over $from and $to ($row too with `rows`, whose columns named like a field fill it).
- `where`: text
