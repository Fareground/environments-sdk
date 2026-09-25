# economy / replenishment

### `economy.replenishment`
Inventory policies for a demand mechanism's items: orders travel in a per-item pipeline for a lead time (fixed, or drawn per order from a noise pattern fitted from purchase orders) and arrive at the start of a round; at the end of every round, after the sales, each item's position (on hand + on order − backorders) is reviewed and the `policy` orders: s_S, s_Q, order_up_to (base stock, periodic with review_every), service (order up to the forecast over lead time + review plus z·σ safety stock for the service_level, σ from the forecast's error and the lead time's spread), custom (`decide`) or manual (agents' <name>_order tool and the `order` action). Orders respect case_pack, min_order, max_order, capacity and a round's budget, and are paid from a ledger account. Holding, ordering, stockout and backorder costs accrue per item; outputs <name>_orders, _purchases, _holding_cost, _order_cost, _stockout_cost, _backorder_cost, _total_cost, _profit (the demand's margin less these costs) and _average_stock_value.

Config:
- `demand` (required): The economy.demand mechanism whose items and stock this keeps (declared before it).
- `policy` (default "s_S"): s_S: when the position is at or below reorder_point, order up to order_up_to | s_Q: order order_qty (repeated until above the reorder point) | order_up_to: order up to order_up_to every review (base stock; periodic with review_every) | service: order up to $target, from service_level | custom: order what `decide` gives | manual: only orders placed by agents or effects. Or an expression over $inputs and $it giving one (for arms).
- `reorder_point` (default null): s, as an expression over $it and $position (on hand + on order − backorders), $on_hand, $on_order, $backlog, $forecast and $forecast_sd (demand per round), $recent (average sales of the kept rounds), $lead_time and $lead_time_sd (rounds), $cover (lead time + review period), $safety (the service level's safety stock) and $target (the service level's order-up-to point).
- `order_up_to` (default null): S, as an expression over the same locals.
- `order_qty` (default null): Q, as an expression over the same locals.
- `decide` (default null): custom: units to order now, as an expression over the same locals.
- `review_every` (default 1): Rounds between reviews (number or expression over $it).
- `service_level` (default 0.95): Chance of not running out before the next order can arrive (the service policy, and $safety and $target).
- `forecast` (default "model"): Demand per round the policies read: model (the demand mechanism's expected demand and its variance, averaged over its kept rounds, so one promotion week does not swing it) | recent (the average and spread of kept sales) | an expression over $it.
- `forecast_sd` (default null): Standard deviation of demand per round (default: from the forecast: the model's variance, the recent spread, or √forecast).
- `lead_time` (default 1.0): Clock units from order to arrival: a number or an expression over $it (known), or {pattern, key, scale} drawn per order. An order arrives at the start of the round that many units later (at least the next).
- `case_pack` (default 1): Orders come in multiples of this (expression over $it).
- `min_order` (default 0): Smallest order (expression over $it).
- `max_order` (default null): Largest order (expression over $it).
- `capacity` (default null): Most units on hand plus on order (expression over $it).
- `budget` (default null): Money for orders each round across all items (expression); the most urgent items are ordered first.
- `unit_cost` (default null): Purchase price per unit (default: the demand's cost).
- `holding_cost` (default 0.0): Cost per unit on hand per round (expression over $it).
- `order_cost` (default 0.0): Cost per order placed (expression over $it).
- `stockout_cost` (default 0.0): Cost per unit of lost demand (expression over $it).
- `backorder_cost` (default 0.0): Cost per unit on backorder per round (expression over $it).
- `account` (default null): Entity id paying for orders from its ledger balance (default: the demand's account); orders it cannot afford are cut.
- `currency` (default null): The ledger currency of `account` (default: the demand's).
- `who` (default null): Agent type(s) that may order with the tool <name>_order.
- `record` (default false): Post every order to the record <name>_orders when it arrives: time, item, placed, arrived, qty, lead_time (clock units) and factor (the draw ÷ scale) — rows to fit a lead-time pattern from.

Nested config:
**LeadTimeRef** — A lead time drawn for every order from a noise pattern (fitted from purchase orders, say).
- `pattern`: text (required) — A noise pattern (normal, lognormal, uniform or laplace), drawn once per order.
- `key`: expression — Its key as an expression over $it, for a keyed pattern (default: the item).
- `scale`: number | text = 1.0 — Multiplies the draw (expression over $it): "$it.lead_weeks" with a lognormal factor around 1.

Actions of the `economy` op:
- `order` — takes `item`, `qty` (needs `item`, `qty`): {"economy": "reorder", "action": "order", "item": "$params.item", "qty": 24}  (order stock now: rounded to the case pack, within capacity, the budget and the account)

```json
{"mechanisms": {"my_replenishment": {"kind": "economy", "mode": "replenishment", "demand": "shop", "policy": "service", "service_level": 0.95, "lead_time": {"pattern": "lead_noise", "scale": "$it.lead_weeks"}, "case_pack": "$it.case_pack", "holding_cost": "$it.unit_cost * 0.004", "order_cost": 6}}}
```
