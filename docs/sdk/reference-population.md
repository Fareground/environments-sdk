# population

## `population`: [PopulationSpec]

Generated entities: a count, or one per data row, with sampled traits.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| where/weight | $row |
| props/id/name | $row $i ($i counts from 1) |
| brief | $actor $row $i |

**PopulationSpec** — Entities generated at load: a count, one per table row, or a weighted sample of rows.
- `type`: text (required)
- `count`: int | text — How many (number or expression). Omit with `from` = one per row.
- `from`: text — Expression giving rows (e.g. $inputs.households).
- `where`: text — Row filter ($row).
- `weight`: text — Row sampling weight ($row); sampled without replacement.
- `replace`: bool = false — Sample rows with replacement.
- `id`: text — Id template ({$i}, {$row.x}); default <type>_<n>.
- `name`: text — Name template.
- `props`: object — Values or expressions ($row, $i, $normal(...)).
- `at`: any
- `brief`: text — Private text added to each generated entity's brief (template over $row, $i).
- `mix`: [MixSpec] — Archetypes: each entity belongs to one, with its own traits and brief; the type's `archetype` prop (if declared) records which.
- `quota`: bool = true — Mix counts are exact shares (largest remainder) instead of independent draws.
- `members`: [MembersSpec] — Entities generated inside each one (households → people).
- `raking`: RakingSpec — Reweight `from` rows to match margins before sampling (uses `weight` as the base weight).
