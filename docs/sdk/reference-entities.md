# entities

## `entities`: {id: EntitySpec}

Named entities (the name defaults to the id), and generated ones: `count` of them, or one per data row (`from`), with sampled traits; ids `<key>_<n>`.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| brief | $actor (generated: $row $i too) |
| where/weight (generated) | $row |
| props/id/name (generated) | $row $i ($i counts from 1) |

**EntitySpec** — A starting entity, whose id is its key. With ``count`` or ``from`` it generates many instead (households from
a table, a crowd of traders): their ids are ``<key>_<n>`` (a row's own ``id``, or the ``id`` template, when given),
and an id already taken is an error. Entities are built in the order they are declared.
- `type`: text (required)
- `name`: text — Its name (default: the id). Generated: a template over $row and $i (default: the type's title and the number).
- `props`: object — Values or expressions ($row, $i, $normal(...) when generated).
- `at`: any
- `brief`: text — Private text added to this entity's own brief (template; over $row and $i when generated).
- `count`: int | text — Generate this many (number or expression). Omit with `from` = one per row.
- `from`: text — Generate from rows: an expression giving them (e.g. $inputs.households).
- `where`: text — Generated: row filter ($row).
- `weight`: text — Generated: row sampling weight ($row); sampled without replacement.
- `replace`: bool = false — Generated: sample rows with replacement.
- `id`: text — Generated: id template ({$i}, {$row.x}); default <key>_<n>.
