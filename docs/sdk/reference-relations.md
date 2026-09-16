# relations

## `relations`: {relation: RelationSpec}

Typed links between entities (trust, follows), with fields.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| props.*.default | $from $to |

**RelationSpec** — A kind of link between entities (follows, trusts, owns …). Every link carries a number
(``value``) and, with ``props``, typed fields of its own (``since``, ``channel``, ``strength``).
- `symmetric`: bool = false
- `default`: number — Value of a link made without one (default 1).
- `min`: number
- `max`: number
- `props`: object — Typed fields every link carries, read as $link(a, b, kind).field; defaults may be expressions over $from and $to.
- `description`: text
