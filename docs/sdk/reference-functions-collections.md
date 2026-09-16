# functions / collections

## Functions: collections

- `$all(items, where)` — True when every item matches (and for no items).
- `$any(items, where)` — True when at least one item matches.
- `$avg(items, value?, where?)` — Mean of `value` over matching items (nulls skipped); null when none. $avg(list) averages a list.
- `$bottom(items, by, n?, where?)` — Items sorted by `by`, lowest first; the first `n` when given.
- `$count(items, where?)` — How many items (entities of a type, or a list) match `where`.
- `$dict(items, key, value)` — A {key: value} map with one entry per item (later items win).
- `$filter(items, where)` — The items for which `where` holds.
- `$first(list)` — First element, or null for an empty list.
- `$flatten(lists)` — One list from a list of lists (one level).
- `$get(object, key, default?)` — Field `key` of an entity, map or record, or element `key` of a list; `default` when missing.
- `$ids(items)` — The ids of the entities given.
- `$is(entity, type)` — True when the entity is of `type` or a type that extends it.
- `$keys(map)` — The keys of a map.
- `$last(list)` — Last element, or null for an empty list.
- `$len(value)` — Length of a list or text.
- `$map(items, value)` — `value` computed for each item.
- `$max(items, value, where?) | max(list) | max(a, b, ...)` — Largest `value` over matching items, of a list, or of the numbers given.
- `$median(items, value?, where?)` — Median of `value` over matching items, or of a list (nulls skipped); null when none.
- `$min(items, value, where?) | min(list) | min(a, b, ...)` — Smallest `value` over matching items, of a list, or of the numbers given.
- `$mode(list)` — The most frequent value (first seen wins ties), or null for an empty list.
- `$pick(items, where?)` — The first matching item, or null.
- `$quantile(items, value, q, where?)` — The q-quantile (0–1, linear interpolation) of `value`; null when none.
- `$range(n) | range(start, end)` — Whole numbers 0..n-1, or start..end-1.
- `$reverse(list)` — The list in reverse order.
- `$slice(list, start, end?)` — Elements from `start` up to (not including) `end`; negative counts from the end.
- `$sort(items, by?)` — Items in ascending order of `by` (a value or list of values; default the items themselves).
- `$stdev(items, value?, where?)` — Sample standard deviation of `value`, or of a list; null when fewer than two.
- `$sum(items, value?, where?)` — Total of `value` over items matching `where`; $sum(list) adds a list of numbers.
- `$tally(list)` — Counts of each distinct value, as a {value: count} map (order of first appearance).
- `$top(items, by, n?, where?)` — Items sorted by `by` (a value or a list of values), highest first; the first `n` when given.
- `$unique(list)` — The list with duplicates removed, order kept.
- `$values(map)` — The values of a map.
