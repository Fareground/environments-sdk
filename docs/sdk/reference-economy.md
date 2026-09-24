# economy

## Mechanism family `economy`

Money, goods and making things: ledgers (currencies, taxes, loans), inventories, production, supply chains, customers' demand for stocked items and the policies that replenish them.

Named the same in every mode:
- `who`: agent type(s) holding money or goods

Modes (`"kind": "economy", "mode": ...`; read one with `guide('economy.<mode>')`):
- `inventory`: Goods held by entities: stackable items in a map property listing each one (`$actor.goods.bread`, 0 when none are held) and unique items as entities with an owner.
- `ledger`: Money: each currency is a number property of every holder (`$actor.cash`) with an optional credit limit.
- `production`: Recipes that turn goods into goods: inputs used up, outputs made, optional skill level, tools held, place, money cost and production time.
- `supply_chain`: A serial supply chain (the beer game as data): each round every node receives what reached it, gets its order (customers' demand at the first node), ships what it can toward that order plus backlog and pays holding and backlog costs; then nodes order from the node upstream (the producer starts a batch) with `<name>_order`, one order a round.
- `demand`: Customers' demand for stocked items, drawn from patterns and served from stock.
- `replenishment`: Inventory policies for a demand mechanism's items: orders travel in a per-item pipeline for a lead time (fixed, or drawn per order from a noise pattern fitted from purchase orders) and arrive at the start of a round; at the end of every round, after the sales, each item's position (on hand + on order − backorders) is reviewed and the `policy` orders: s_S, s_Q, order_up_to (base stock, periodic with review_every), service (order up to the forecast over lead time + review plus z·σ safety stock for the service_level, σ from the forecast's error and the lead time's spread), custom (`decide`) or manual (agents' <name>_order tool and the `order` action).

Functions:
- `$conserved(ledger_or_inventory)` — True while every currency or item of a ledger or inventory adds up to its supply (and no balance passes its credit limit).
- `$count_items(agent, item?)` — How many of `item` the agent holds; without `item`, how many goods of every kind.
- `$demand_totals(mechanism, measure, by?)` — A demand mechanism's run total: demand, served, substituted, backordered, lost, spill_in, sold, returned, revenue, refunds, cogs, fill_rate, net_revenue or margin — overall, or {key: total} by 'item', 'group' or 'segment'.
- `$ground_items(inventory, place)` — Stackable items lying at a place: {item: qty}.
- `$has(agent, asset, qty?)` — True when the agent holds at least `qty` (default 1) of an item or currency: $has($actor, bread, 2), $has($actor, cash, 5).
- `$items_text(agent, inventory?)` — The agent's goods as plain words: '3 bread, 2 flour, room for 5 more'.
- `$loose_total(inventory, item)` — Items nobody holds: on the ground, or unique items whose owner is gone.
- `$max_batches(agent, production, recipe)` — Most batches of a recipe the agent can start now (0 when it cannot).
- `$money_held(ledger)` — Each currency of a ledger as held now, {currency: total}: every holder's balance and what markets hold for their traders (escrow, reserves, vaults, fees). The ledger's supply starts at it.
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
