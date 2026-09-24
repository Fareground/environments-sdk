# functions / lists

## Functions: lists

- `$chunk(list, size)` — The list cut into consecutive pieces of `size` items (the last may be shorter).
- `$cumsum(series)` — Running totals: item i is the sum of items 0..i.
- `$diff(series)` — Differences between consecutive items (one shorter than the series).
- `$difference(a, b)` — Items of `a` that are not in `b`, each once, in `a`'s order.
- `$index(list, value)` — Position of the first item equal to `value` (entities match their id), or -1. In text, the position of the first `value` in it (case-sensitive), or -1.
- `$insert(list, index, value)` — A copy of the list with `value` inserted before position `index` (the length appends).
- `$intersect(a, b)` — Items of `a` that are also in `b`, each once, in `a`'s order.
- `$items(map)` — The entries of a map as [key, value] pairs.
- `$lookup(table, field, key)` — The rows of `table` whose `field` equals `key`, in table order (an empty list when none): $lookup($inputs.sales, sku, $row.sku) finds them through an index built once per run instead of scanning the table for every SKU. `field` may be a list of fields with `key` a list of values.
- `$lookup_one(table, field, key, default?)` — The first row of `table` whose `field` equals `key`, or `default` (null) when none: $lookup_one($inputs.models, model, $row.model).msrp. Indexed like $lookup.
- `$merge(map, map, ...)` — One map from several; a key in a later map wins.
- `$pick_keys(map, keys)` — A map with only `keys` (one key or a list), in the order given; missing keys are skipped.
- `$rank(items, value?)` — Rank of each item, 1 = largest; ties share the best rank (1, 2, 2, 4); a null value ranks null.
- `$remove_at(list, index)` — A copy of the list without the item at `index`.
- `$rotate(list, k)` — The list rotated left by `k` places (negative rotates right).
- `$set_at(list, index, value)` — A copy of the list with the item at `index` replaced by `value`.
- `$union(a, b)` — Items in either list, each once, in order of first appearance.
- `$window(list, size, step?)` — Sliding windows of `size` consecutive items, starting every `step` items (default 1).
- `$without(map, keys)` — A copy of the map without `keys` (one key or a list); missing keys are ignored.
- `$zip(a, b, ...)` — Lists combined position by position into [a_i, b_i, ...] (as long as the shortest).
