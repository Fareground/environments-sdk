# Corner shop inventory

A small coffee shop sells three products: coffee beans (cost $6, price $10), mugs (cost $4, price $9) and paper filters (cost $1, price $3). The owner, an AI agent, decides every week how many units of each product to order from the supplier. Orders are paid for when placed and arrive at the start of the following week. The shop opens with $500 in cash and 10 units of each product. Each week customers want a random number of each product, and anything the shop cannot supply is a lost sale; unsold stock simply stays on the shelf. The owner can never spend more cash than the shop has. Customer demand must not depend on the owner's choices, so two ordering strategies can be compared on exactly the same weeks. Run it for twelve weeks.

## Deliverables

Report these outputs, using `beans`, `mugs` and `filters` as the keys of every per-product map:
- `cash_end` — cash at the end.
- `units_ordered` — per product, the total units ordered.
- `units_sold` — per product, the total units sold.
- `units_on_hand_end` — per product, the stock on the shelf at the end.
- `units_on_order_end` — per product, units ordered but not yet arrived at the end.
- `lost_sales` — per product, total units customers wanted but could not get.
- `demand_by_week` — per product, a list of the units customers wanted each week, in order.
