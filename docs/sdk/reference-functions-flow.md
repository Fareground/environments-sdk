# functions / flow

## Functions: flow

- `$best(items, by, ties?)` — The best of `items` by `by` (a value or list of values, highest first): one item, a list when tied and ties is 'share' (default), null when tied and ties is 'none', one at random (seeded) when 'random'; null when empty.
- `$stack(procedure, read?, ...)` — A procedure's response stack. read: items (default; bottom first: [{id, kind, title, by, params, on, round, waiting}], `on` the id of the item it answers, `waiting` who still owes it an answer) | top (the top item or null) | waiting (ids who owe the top an answer; with an agent, whether it does) | can_push (kind, agent: whether it may push that kind now) | text (viewer?: the stack as lines, with what the viewer may answer).
- `$turn_order(order)` — The agents in this round's turn order (skipped agents left out).
- `$turn_rank(entity, order)` — The entity's place (0 = first) in this round's turn order; skipped agents come last.
- `$won(entity, victory)` — The entity's share of the win once the victory mechanism has ended the run: 1 for the winner (or every player of the winning team), 1/n when n players share the win, 0 otherwise and while the run goes on; e.g. $won($actor, 'victory').
