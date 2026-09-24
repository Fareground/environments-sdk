# market / order_book

### `market.order_book`
One instrument on a continuous limit order book with price-time priority, partial fills, tick and lot sizes, maker/taker fees, market-order collars, optional short selling, order expiry, OHLCV bars of `bar_rounds` rounds and a circuit breaker (measured from the round's open, the bar's open or a rolling window; checked on every trade or at each round's end; halting for some rounds or to the end of the bar). The venue's numbers (tick_size, lot_size, fees, collar_pct, price_band_pct, halt_pct, halt_rounds, halt_window, short_limit, max_short_leverage, order_ttl, max_orders, bar_rounds) may be expressions over $inputs (like world defaults), resolved once when the world is built into $world.<name>_rules, so they can follow the price level and be swept or calibrated. Tools `<name>_buy` / `<name>_sell` (limit with a price, market without), `<name>_cancel`, `<name>_cancel_all` and `<name>_algo` (coded strategy). Each `who` trader holds the instrument in `<name>_shares` and money in `currency` (give traders their starting position there, e.g. "acme_shares": 100). Resting orders reserve cash or shares; trades settle with conserved transfers and fees go to $world.<name>_fees. Read the book with $book(name), $book_depth(name, levels, viewer), $book_orders(name, trader), $book_account(name, trader); trades are in the `<name>_tape` record and OHLCV bars {bar, open, high, low, close, volume, vwap, trades, halted, flow} in `<name>_bars`; $book(name).bar is the bar in progress. The book opens and closes each round once, in its own start and end events, which run after yours: an end event that reads the round or bar the book closes runs {"market": name, "action": "close"} first. `crowd` adds coded traders (market_maker, momentum, mean_reversion, fundamentalist, noise, passive) as types `<name>_<strategy>` extending `<name>_crowd`, which holds what a `who` trader holds but is not of that type, so other mechanisms on `who` (a ballot, a winner's candidates) leave the crowd out; any trader whose `<name>_strategy` prop names a strategy trades only through `<name>_algo` (the `<name>_algo` policy calls it). A strategy with a `stop_loss` param (in multiples of the per-round volatility) liquidates a losing position at market. $book(name).flow is the last round's aggressive quantity by trader kind. Fundamentalists estimate `fair_value`, by default $world.<name>_value: a random walk from the start price at the book's `volatility`. Metrics <name>_price (last trade), _mid (mid quote: returns without the bid-ask bounce), _volume, _spread, _orders feed $market_realism.

Config:
- `who` (required): Agent type that trades (subtypes included).
- `start_price` (required): Opening reference price (number or expression).
- `currency` (default "cash"): Trader property holding money (added with 0 if the type lacks it).
- `instrument` (default ""): Display name of the instrument (default: the book's name).
- `tick_size` (default 0.01): Minimum price increment (number or expression over $inputs, resolved when the world is built).
- `lot_size` (default 1.0): Minimum quantity; orders are whole multiples of it (number or expression over $inputs, resolved when the world is built). A literal whole lot makes order quantities integers.
- `maker_fee_bps` (default 0.0): Fee on fills of resting orders, in basis points of notional (number or expression over $inputs, resolved when the world is built); negative is a rebate, at most the taker fee that pays it.
- `taker_fee_bps` (default 0.0): Fee on fills of incoming orders, in basis points (number or expression over $inputs, resolved when the world is built).
- `collar_pct` (default 0.05): A market order never trades further than this from the touch (number or expression over $inputs, resolved when the world is built).
- `price_band_pct` (default 0.5): Limit prices must be within this fraction of the round's opening price (number or expression over $inputs, resolved when the world is built).
- `halt_pct` (default null): Circuit breaker: halt when the price moves this far from the reference price (number or expression over $inputs, resolved when the world is built); null or 0 = no breaker.
- `halt_reference` (default "round_open"): What the breaker measures a move from: round_open (the last price when the round opened), bar_open (when the bar opened; on a continuous book that is also the previous bar's close) or rolling (the close `halt_window` rounds back).
- `halt_window` (default null): Rounds a rolling reference looks back (1 = the round's open) (number or expression over $inputs, resolved when the world is built).
- `halt_check` (default "trade"): When the breaker looks: trade (every trade price; the order that trips it stops there) or round_end (the mid when each round closes, before its bar is recorded).
- `halt_until` (default "rounds"): How long a halt lasts: rounds (the rest of the round and `halt_rounds` more) or bar_end (to the end of the bar it trips in).
- `halt_rounds` (default 1): Extra rounds a halt lasts after the round it trips (halt_until rounds) (number or expression over $inputs, resolved when the world is built).
- `short_limit` (default 0.0): How far below zero a trader's shares may go (0 = no short selling) (number or expression over $inputs, resolved when the world is built).
- `max_short_leverage` (default null): A sell is refused when the short position it could leave would be worth more than this multiple of the trader's equity (short_limit stays the absolute cap) (number or expression over $inputs, resolved when the world is built).
- `order_ttl` (default null): Resting orders expire after this many rounds (number or expression over $inputs, resolved when the world is built).
- `max_orders` (default 20): Resting orders one trader may have (number or expression over $inputs, resolved when the world is built).
- `bar_rounds` (default 1): Rounds in one OHLCV bar of the `<name>_bars` record (a bar of several passes) (number or expression over $inputs, resolved when the world is built); $book(name).bar is the bar in progress.
- `depth_levels` (default 5): Price levels per side shown in the book view.
- `tape` (default 50): Recent trades kept in the <name>_tape record.
- `volatility` (default 0.02): Per-round return volatility the default fair value walks at, that market makers price their spread and their reading of order flow by, and other coded strategies assume before the tape shows one (number or expression). Market makers move their quotes with net order flow, so informed traders carry the price toward the value; without fundamentalists it wanders with the noise.
- `measure_volatility` (default true): Coded strategies other than market makers measure volatility from recent closes; false makes them always assume `volatility` (a calibrated value).
- `base_qty` (default null): Coded strategies' unit of order size (default 10 lots); an expression is read on every turn, so a controller can steer it.
- `flow_scale` (default null): Expression multiplying speculative order sizes (momentum, noise, passive); default 1.
- `sentiment` (default null): Expression for market sentiment in [-1, 1] that tilts noise traders toward buying or selling; default 0.
- `fair_value` (default null): Expression for the true value fundamentalists estimate (default: $world.<name>_value, a random walk from the start price at `volatility`).
- `crowd` (default {}): Coded traders by strategy: {market_maker: {count, cash, shares, params}}.
- `stage` (default null): Trade during this declared stage; default: a sequential stage named after the book.
- `max_actions` (default 4): Actions per turn in the generated stage.
- `conserve` (default true): Declare the invariant that reserves match the book and balances stay within limits: true or action (checked after every action), round (after every round: much cheaper for big crowds), end (once, when the run finishes), or false.

Nested config:
**CrowdSpec** — A group of coded traders generated for the book.
- `count`: int | text (required) — How many (number or expression).
- `cash`: number | text = 0.0 — Starting cash each (number or expression).
- `shares`: number | text = 0.0 — Starting shares each (number or expression).
- `params`: object — Strategy parameter overrides (see the guide): numbers, or expressions read once per trader when its parameters are drawn.

Actions of the `market` op:
- `buy` — takes `who`, `qty`, `price` (needs `qty`): {"market": "acme", "action": "buy", "qty": 10, "price": 50.5}  (a limit buy with a price, a market buy without)
- `sell` — takes `who`, `qty`, `price` (needs `qty`): {"market": "acme", "action": "sell", "qty": 10}  (a limit sell with a price, a market sell without)
- `cancel` — takes `who`, `order` (needs `order`): {"market": "acme", "action": "cancel", "order": "$params.order"}  (cancel one resting order by id)
- `cancel_all` — takes `who`: {"market": "acme", "action": "cancel_all"}  (cancel every resting order of the trader)
- `algo` — takes `who`: {"market": "acme", "action": "algo"}  (let the trader's coded strategy act once)
- `open`: {"market": "acme", "action": "open"}  (open the round (once a round; the book's own start event runs it after yours): expire orders, resume after a halt, start a bar, reset the round)
- `close`: {"market": "acme", "action": "close"}  (close the round (once a round; the book's own end event runs it after yours, so run it first in an end event that reads the closed round or bar): breaker check, record the bar)

```json
{"mechanisms": {"my_order_book": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50, "tick_size": 0.01, "taker_fee_bps": 5, "halt_pct": 0.1, "crowd": {"market_maker": {"count": 2, "cash": 20000, "shares": 400}, "noise": {"count": 6, "cash": 5000, "shares": 100}}}}}
```
