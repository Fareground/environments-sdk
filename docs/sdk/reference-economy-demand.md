# economy / demand

### `economy.demand`
Customers' demand for stocked items, drawn from patterns and served from stock. Each round an item's expected demand is `rate` × `factors` (patterns: base, season, trend, price elasticity with its driver, promotion, cross-price substitution, drivers), a counts pattern (`noise`) draws the units, and sales are capped by `stock`: unmet demand partly buys `substitutes` (`spill`), partly waits (`backorder`) and the rest is lost — true demand and lost sales are kept apart. `segments` add bulk buyers or channels with their own rate, price, items and `returns` (rate, delay, restock). Prices and promotions are read at the start of the round, sales at its end; revenue goes to a ledger `account`. Item props <name>_price, _promo, _expected, _variance (of this round's demand), _demand, _sold, _lost, _stockout, _backlog, _recent and totals; outputs <name>_demand, _sold, _lost, _fill_rate, _revenue, _margin, _returned, by item and by `group`; metrics per round. Stock changes only through sales, returns and the `receive`/`remove` actions (invariant $stock_conserved). `record` posts <name>_history rows (time, item, segment, units, stockout, demand, lost, stock, price, promo, each driver) ready for fit_patterns.

Config:
- `items` (required): Entity type of the items (one entity per SKU or product, e.g. generated from a table).
- `rate` (required): Expected units a round for each item before its factors: a number, an expression over $it, or a pattern (its name, or {pattern, key}; a keyed pattern reads the item). This is the main segment; `segments` adds others.
- `factors` (default []): Multipliers of the rate, each a pattern name, {pattern, key, driver} or an expression over $it and $price: a response (elasticity) is called with its driver (default $price), a cross_price pattern with every item's price, any other pattern (season, promotion, trend) is read for the item.
- `noise` (default null): A counts pattern drawing whole units around the expected demand (Poisson when omitted). An unkeyed one gives every item and segment its own draw.
- `returns` (default null): Units the main segment sends back: {rate, delay, restock}.
- `stock` (default null): Int property of each item holding the units on hand; sales are capped by it (generated when the type lacks it). Without it all demand is served.
- `price` (default "$it.price"): Each item's price this round, as an expression over $it; read at the start of every round into <name>_price.
- `promotion` (default 0.0): Each item's promotion depth this round (0.2 = 20% off), as an expression over $it; read into <name>_promo before `price`, so the price and a promotion pattern (input $it.<name>_promo) can use it.
- `cost` (default 0.0): Unit cost of an item (expression over $it), for margins.
- `group` (default null): Each item's group (a category), as an expression over $it, for totals by group.
- `segment` (default "retail"): Name of the main segment.
- `segments` (default {}): Further segments with their own demand — bulk buyers, channels: {name: {rate, factors, noise, price, where, returns}}, served after the main one, in the order listed.
- `substitutes` (default null): Items a customer who finds an item out of stock tries instead, in order: an expression over $it giving ids or entities ([$it.sibling]).
- `spill` (default 0.0): Share of unmet demand that tries the substitutes (expression over $it).
- `backorder` (default 0.0): Share of the demand still unmet that waits for stock (a backorder, served first when stock arrives) instead of leaving.
- `recent` (default 8): Rounds of sales each item keeps in <name>_recent, oldest first.
- `account` (default null): Entity id whose ledger balance receives revenue and pays refunds.
- `currency` (default null): The ledger currency of `account`.
- `record` (default false): Post each item's round, per segment, to the record <name>_history (true, or an expression over $inputs): the columns fit_patterns reads.

Nested config:
**FactorRef** — A pattern read for each item: its key, and for a response what it answers.
- `pattern`: text (required) — A declared pattern.
- `key`: expression — Its key, as an expression over $it (default: the item, for a pattern with keys).
- `driver`: expression — For a response (elasticity, saturation …): what it is called with, as an expression over $it and $price (default: $price, the price paid).
**ReturnsSpec** — Units a segment sends back.
- `rate`: number | text (required) — Share of units sold that come back (number or expression over $it).
- `delay`: int | text = 1 — Rounds until they come back (number or expression over $it).
- `restock`: number | text = 1.0 — Share of returned units fit to sell again; the rest are scrapped.
**SegmentSpec** — A customer segment or sales channel with its own demand.
- `rate`: number | text | FactorRef (required) — Expected units a round for each item before its factors: a number, an expression over $it, or a pattern (its name, or {pattern, key}; a keyed pattern reads the item).
- `factors`: [number | text | FactorRef] — Multipliers of the rate, each a pattern name, {pattern, key, driver} or an expression over $it and $price: a response (elasticity) is called with its driver (default $price), a cross_price pattern with every item's price, any other pattern (season, promotion, trend) is read for the item.
- `noise`: text — A counts pattern drawing whole units around the expected demand (Poisson when omitted). An unkeyed one gives every item and segment its own draw.
- `price`: expression — What the segment pays per unit, as an expression over $it and $price (the item's price): "$price * 0.85". Default $price.
- `where`: expression — Only items where this holds ($it).
- `returns`: ReturnsSpec — Units this segment sends back: {rate, delay, restock}.

Actions of the `economy` op:
- `receive` — takes `item`, `qty`, `source` (needs `item`, `qty`, `source`): {"economy": "shop", "action": "receive", "item": "$it", "qty": 12, "source": "supplier"}  (units enter an item's stock from a named source)
- `remove` — takes `item`, `qty`, `sink` (needs `item`, `qty`, `sink`): {"economy": "shop", "action": "remove", "item": "$it", "qty": 2, "sink": "damaged"}  (units leave an item's stock into a named sink; refused when fewer are on hand)

```json
{"mechanisms": {"my_demand": {"kind": "economy", "mode": "demand", "items": "sku", "stock": "stock", "price": "$it.list_price * (1 - $it.shop_promo)", "promotion": "0.2 if $pattern.promo_week($it.category) > 0 else 0", "cost": "$it.unit_cost", "group": "$it.category", "rate": "demand", "factors": [{"pattern": "price_effect", "key": "$it.category", "driver": "$price / $it.list_price"}, "promo"], "noise": "sales", "substitutes": "[$it.sibling]", "spill": 0.3, "segments": {"repair_shops": {"rate": "$it.shop_rate", "price": "$price * 0.85"}}}}}
```
