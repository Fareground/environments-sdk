# market / auction

### `market.auction`
An auction: sealed first_price, second_price (Vickrey), english (ascending, increment, timeout), dutch (falling clock), double (call market at one price) or uniform (multi-unit, one price). Tools `<name>_bid` (price, qty) and, for double, `<name>_ask`. Bids escrow cash, asks escrow units; proceeds go to the `house` entity or $world.<name>_revenue. Each closed lot is posted to the `<name>_results` record (winner, price, qty, lot, note): read the last sale as $auction(<name>).last.winner and .price: null before the first lot closes, kept until another closes. The other fields of $auction(name) describe the open lot; $auction_text(name, viewer) describes it.

Config:
- `format` (required): first_price | second_price (Vickrey) | english | dutch | double | uniform (multi-unit) | combinatorial (package bids on `items`).
- `who` (required): Agent type that bids (subtypes included).
- `sellers` (default null): double: agent type that asks (default: `who`).
- `currency` (default "cash"): Property holding money.
- `item` (default "lot"): What is sold, in plain words.
- `house` (default null): Entity id of the auction house: sells its units and is paid; default: the mechanism itself (stock and revenue in world props).
- `stock` (default 1): Units the house has to sell (number or expression).
- `units` (default 1): Units in each lot (uniform; the last lot sells what is left), or the most units one bid or ask may carry (double).
- `reserve` (default 0.0): Lowest acceptable price per unit (number or expression).
- `start_price` (default null): dutch: where the clock starts.
- `decrement` (default 1): dutch: how much the clock falls each round.
- `increment` (default 1): english: minimum raise over the high bid.
- `timeout` (default 1): english: rounds without a new bid before the lot closes.
- `ties` (default "first"): Equal bids: the earliest wins (sealed bids arrive in their stage's commit order: random unless it sets `order`), or a seeded random one.
- `price_rule` (default "lowest_accepted"): uniform: the clearing price — the lowest accepted bid, or the highest rejected one (the reserve when none was rejected).
- `items` (default []): combinatorial: the distinct items for sale, bid on in packages.
- `reserves` (default {}): combinatorial: reserve per item (number or expression); others use `reserve`.
- `packages` (default 3): combinatorial: most package bids one bidder may hold (it wins at most one).
- `payment` (default "vcg"): combinatorial: vcg (winners pay the value they displace; truthful bids are safe) | pay_bid.
- `when` (default null): Open lots only when true (e.g. "$round <= 3").
- `stage` (default null): Bid during this declared stage; default: a stage named after the auction.
- `conserve` (default true): Declare invariants that cash and units are conserved and escrow matches bids.
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Actions of the `market` op:
- `bid` — takes `who`, `price`, `qty`, `items` (needs `price`): {"market": "house", "action": "bid", "price": 120}  (bid on the open lot; `qty` for uniform and double, `items` lists a combinatorial package)
- `ask` — takes `who`, `price`, `qty` (needs `price`): {"market": "house", "action": "ask", "price": 90, "qty": 1}  (offer units in a double auction)
- `rebase`: {"market": "house", "action": "rebase"}  (take current cash and unit totals as the supply the invariants conserve)

```json
{"mechanisms": {"my_auction": {"kind": "market", "mode": "auction", "format": "second_price", "who": "collector", "item": "a painting", "stock": 3, "reserve": 50}}}
```
