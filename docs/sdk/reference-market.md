# market

## Mechanism family `market`

Trading venues: continuous order books, auctions and procurement tenders, prediction markets and posted-price shops.

Named the same in every mode:
- `who`: agent type that trades (in effects: the trading agent, default $actor)
- `currency`: the property (or ledger currency) holding money
- `qty`: units traded (shares, items); money is `amount`
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "market", "mode": ...`; read one with `guide('market.<mode>')`):
- `order_book`: One instrument on a continuous limit order book with price-time priority, partial fills, tick and lot sizes, maker/taker fees, market-order collars, optional short selling, order expiry, OHLCV bars of `bar_rounds` rounds and a circuit breaker (measured from the round's open, the bar's open or a rolling window; checked on every trade or at each round's end; halting for some rounds or to the end of the bar).
- `prediction`: A market on which of several outcomes happens, priced by an automated market maker (lmsr or cpmm).
- `auction`: An auction: sealed first_price, second_price (Vickrey), english (ascending, increment, timeout), dutch (falling clock), double (call market at one price) or uniform (multi-unit, one price).
- `posted`: A market of listings at posted prices with stock, per-round capacity, promotions, sponsored placement, ratings and haggling (an offer at or above the floor is accepted, below it the seller counters at the floor).

Functions:
- `$amm(name)` — A prediction market: {prices: {outcome: price}, vault, fees, volume, resolved, payout, maker, liquidity, question}.
- `$amm_cost(name, outcome, shares)` — What buying `shares` of `outcome` costs now, fees included.
- `$amm_ok(name)` — True while a prediction market's vault covers every share.
- `$amm_outcomes(name, viewer?)` — Each outcome of a prediction market: [{outcome, price, held}] (held by the viewer).
- `$auction(name)` — An auction's state: {format, open, lot, price, leader, min_bid, reserve, bids, sold, revenue, stock, items, last}. price, leader and bids describe the open lot; items what a combinatorial lot still has for sale; last the latest closed lot's result, sold or not: {lot, winner, winners, price, qty, note} (winner: the first winner's id, '' when unsold; price: the first winner's price per unit), kept until another lot closes, null before any has.
- `$auction_ok(name)` — True while an auction's escrow matches its open bids and every item is held once.
- `$auction_text(name, viewer?)` — The open lot as one plain sentence (what is sold, prices, your bids).
- `$book(name)` — Top of an order book: {last, bid, ask, mid, spread, bid_qty, ask_qty, ref, halted, halt_until, open, high, low, round_volume, round_trades, volume, vwap, trades, fees, halts, orders, flow, liquidations, bar, bar_rounds, tick, lot, maker_fee_bps, taker_fee_bps, short_limit, halt_pct, band_low, band_high, heat}; flow is the last round's aggressive {buy, sell} quantity by trader kind; bar is the bar in progress {bar, open, high, low, close, volume, vwap, trades, halted, flow, ends}; heat is recent volatility against the usual (1 = as usual, 0.5–2), which coded traders other than market makers multiply their activity by.
- `$book_account(name, trader)` — A trader's account on a book: {cash, shares, reserved_cash, reserved_shares, position, equity, pnl, fees_paid, orders, max_buy, max_sell}.
- `$book_depth(name, levels?, viewer?)` — Order book price levels as a ladder (asks high→low, then bids high→low): [{side, price, qty, orders, mine}]; `mine` is the viewer's own quantity.
- `$book_ok(name)` — True while the book's accounting holds: reserves equal resting orders, balances within limits, the book in price-time order and never crossed.
- `$book_orders(name, trader)` — A trader's resting orders, best price first: [{id, side, price, qty, seq, round}].
- `$book_rules(name, rules)` — An order book's venue rules {tick_size, lot_size, ...}, each checked against its limits; the book's generated `<name>_rules` world prop resolves its expressions through it.
- `$package_winners(bids, reserves?, payment?)` — Exact winner determination for package bids [{bidder, items, price}] (each bidder wins at most one bid, no item twice): {winners: [{bidder, items, price, pays}], surplus, revenue}. `reserves` is one number per item or {item: reserve}; `payment` vcg (default: winners pay the surplus they displace) or pay_bid.
- `$posted_counters(name, buyer)` — Ids of listings where the buyer holds an open counter-offer.
- `$posted_line(name, listing, viewer?)` — A listing as one shelf line: price, rating, stock, capacity, offers, counters.
- `$posted_ok(name)` — True while no listing of a posted-price market has negative stock.
- `$posted_price(name, listing)` — A listing's asking price now, promotions applied.
- `$shelf(name, item?)` — Listings in stock on a posted-price market, ranked (sponsored, rating, price by default).
