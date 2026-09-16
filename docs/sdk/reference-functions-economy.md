# functions / economy

## Functions: economy

- `$conserved(ledger_or_inventory)` — True while every currency or item of a ledger or inventory adds up to its supply (and no balance passes its credit limit).
- `$count_items(agent, item?)` — How many of `item` the agent holds; without `item`, how many goods of every kind.
- `$demand_totals(mechanism, measure, by?)` — A demand mechanism's run total: demand, served, substituted, backordered, lost, spill_in, sold, returned, revenue, refunds, cogs, fill_rate, net_revenue or margin — overall, or {key: total} by 'item', 'group' or 'segment'.
- `$ground_items(inventory, place)` — Stackable items lying at a place: {item: qty}.
- `$has(agent, asset, qty?)` — True when the agent holds at least `qty` (default 1) of an item or currency: $has($actor, bread, 2), $has($actor, cash, 5).
- `$items_text(agent, inventory?)` — The agent's goods as plain words: '3 bread, 2 flour, room for 5 more'.
- `$loose_total(inventory, item)` — Items nobody holds: on the ground, or unique items whose owner is gone.
- `$max_batches(agent, production, recipe)` — Most batches of a recipe the agent can start now (0 when it cannot).
- `$net_worth(agent, prices?)` — Money (at each currency's value) plus goods (at `prices` {item: price}, else each item's value) plus loans owed to the agent, minus loans it owes.
- `$owned_items(agent, inventory?)` — Items the agent holds now: stackable item names with a quantity above 0 and the ids of unique items it owns.
- `$pipeline(agent, chain)` — Units on their way to a node of a supply chain, slot 0 arriving next round.
- `$pipeline_text(agent, chain)` — Units on their way to a node, in words: '4 next round, 8 in 2 rounds'.
- `$recipes(agent, production)` — Recipes of a production the agent can start now.
- `$recipes_text(agent, production)` — Every recipe as plain lines: what it takes, what it makes, and what stops the agent now.
- `$replenishment_totals(mechanism, measure, by?)` — A replenishment mechanism's total: orders, units_ordered, purchases, holding_cost, order_cost, stockout_cost, backorder_cost, total_cost, on_order or stock_value (units on hand at unit cost, now) — overall, or {key: total} by 'item' or 'group'.
- `$skill(agent, skill)` — The agent's level in a skill (the skill's start level until it gains one).
- `$space_left(agent, inventory)` — Capacity the agent has left in an inventory, or null when unlimited.
- `$stock_conserved(mechanism)` — True while a demand mechanism's stock equals its starting stock plus every recorded flow in and out (sales, returns, receive and remove).
- `$total_held(type, asset)` — Total of a currency or item held by every entity of `type` (goods on their way to them included).
