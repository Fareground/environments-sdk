# market / auction

### `market.auction`
An auction: sealed first_price, second_price (Vickrey), english (ascending, increment, timeout), dutch (falling clock), double (call market at one price) or uniform (multi-unit, one price). Tools `<name>_bid` (price, qty) and, for double, `<name>_ask`. Each party holds the units it bought, or a double auction's sellers the units they offer, in `<name>_units` (give sellers their stock there). Bids escrow cash, asks escrow units; proceeds go to the `house` entity or $world.<name>_revenue. `reverse: true` makes it a procurement tender (the house buys; the lowest offer at or below the reserve wins, is paid and supplies one unit to the house's `<name>_units`, taken from its `deliver_from` stock when set); `score` awards a first_price lot to the best score instead of the best price. Each closed lot is posted to the `<name>_results` record, one entry per winner (winner, price, qty, lot, note; an unsold lot has one entry with winner ''). $auction(<name>).last is the latest closed lot, sold or not: {lot, winner (the first winner, '' when unsold), winners, price (the first winner's price per unit; in a uniform auction every winner pays it), qty (units sold), note}, null before the first lot closes; output `<name>_prices` lists the price of every winning entry. The other fields of $auction(name) describe the open lot; $auction_text(name, viewer) describes it.

Config:
- `format` (required): first_price | second_price (Vickrey) | english | dutch | double | uniform (multi-unit) | combinatorial (package bids on `items`).
- `who` (required): Agent type that bids (subtypes included).
- `sellers` (default null): double: agent type that asks (default: `who`).
- `currency` (default "cash"): Property holding money.
- `item` (default "lot"): What is sold, in plain words.
- `house` (default null): Entity id of the auction house: sells its units and is paid (with `reverse`: buys and pays); default: the mechanism itself (stock and revenue in world props).
- `stock` (default 1): Units the house has to sell, or with `reverse` to buy (number or expression).
- `units` (default 1): Units in each lot (uniform; the last lot sells what is left), or the most units one bid or ask may carry (double).
- `reserve` (default 0.0): Lowest acceptable price per unit (number or expression); with `reverse`, the highest the house pays.
- `reverse` (default false): first_price / second_price: a procurement tender: the `house` buys, the lowest offer wins and is paid (its offer, or the second-lowest).
- `deliver_from` (default null): reverse: the bidders' property holding their stock; the winner's unit comes out of it, so an offer needs one in stock (default: the unit is a service, made on delivery).
- `score` (default null): first_price: award to the acceptable bid with the highest score, an expression over $price and $it (the bidder), e.g. "$it.quality * 10 - $price".
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
- `conserve` (default true): Declare the invariant that escrow matches open bids and every item is held once.

Actions of the `market` op:
- `bid` — takes `who`, `price`, `qty`, `package` (needs `price`): {"market": "house", "action": "bid", "price": 120}  (bid on the open lot; `qty` for uniform and double, `package` lists the items of a combinatorial bid)
- `ask` — takes `who`, `price`, `qty` (needs `price`): {"market": "house", "action": "ask", "price": 90, "qty": 1}  (offer units in a double auction)

```json
{"mechanisms": {"my_auction": {"kind": "market", "mode": "auction", "format": "second_price", "who": "collector", "item": "a painting", "stock": 3, "reserve": 50}}}
```
