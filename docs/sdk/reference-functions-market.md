# functions / market

## Functions: market

- `$amm(name)` — A prediction market: {prices: {outcome: price}, vault, fees, volume, resolved, payout, maker, liquidity, question}.
- `$amm_cost(name, outcome, shares)` — What buying `shares` of `outcome` costs now, fees included.
- `$amm_ok(name)` — True while a prediction market conserves cash and its vault covers every share.
- `$amm_outcomes(name, viewer?)` — Each outcome of a prediction market: [{outcome, price, held}] (held by the viewer).
- `$auction(name)` — An auction's state: {format, open, lot, price, leader, min_bid, reserve, bids, sold, revenue, stock, items, last}. price, leader and bids describe the open lot; items what a combinatorial lot still has for sale; last the latest closed lot's result {lot, winner, winners, price, qty, note} (winner: the first winner's id, '' when unsold), kept until another lot closes, null before any has.
- `$auction_ok(name)` — True while an auction conserves cash and units and escrow matches open bids.
- `$auction_text(name, viewer?)` — The open lot as one plain sentence (what is sold, prices, your bids).
- `$book(name)` — Top of an order book: {last, bid, ask, mid, spread, bid_qty, ask_qty, ref, halted, halt_until, open, high, low, round_volume, round_trades, volume, vwap, trades, fees, halts, orders, flow, liquidations, bar, bar_rounds, tick, lot, maker_fee_bps, taker_fee_bps, short_limit, halt_pct, band_low, band_high}; flow is the last round's aggressive {buy, sell} quantity by trader kind; bar is the bar in progress {bar, open, high, low, close, volume, vwap, trades, halted, flow, ends}.
- `$book_account(name, trader)` — A trader's account on a book: {cash, shares, reserved_cash, reserved_shares, position, equity, pnl, fees_paid, orders, max_buy, max_sell}.
- `$book_depth(name, levels?, viewer?)` — Order book price levels as a ladder (asks high→low, then bids high→low): [{side, price, qty, orders, mine}]; `mine` is the viewer's own quantity.
- `$book_ok(name)` — True while the book's accounting holds: cash and shares conserved, reserves equal resting orders, balances within limits, the book in price-time order and never crossed.
- `$book_orders(name, trader)` — A trader's resting orders, best price first: [{id, side, price, qty, seq, round}].
- `$book_rules(name, rules)` — An order book's venue rules {tick_size, lot_size, ...}, each checked against its limits; the book's generated `<name>_rules` world prop resolves its expressions through it.
- `$excess_kurtosis(values)` — Excess kurtosis of a series (0 for a normal; positive = fat tails).
- `$market_realism(series, reference)` — Realism score of a simulated tape against a reference: {score, components} on volatility, fat tails, no return memory, volatility clustering, volume and volume-volatility correlation. Each side is a price list, {prices, volumes} or $market_stats(...).
- `$market_stats(prices, volumes?)` — Stylized facts of a price series: {bars, sigma, mean_return, kurtosis, acf1, acf_abs, max_drawdown, total_return, avg_volume, vol_volume_corr}.
- `$package_winners(bids, reserves?, payment?)` — Exact winner determination for package bids [{bidder, items, price}] (each bidder wins at most one bid, no item twice): {winners: [{bidder, items, price, pays}], surplus, revenue}. `reserves` is one number per item or {item: reserve}; `payment` vcg (default: winners pay the surplus they displace) or pay_bid.
- `$posted_counters(name, buyer)` — Ids of listings where the buyer holds an open counter-offer.
- `$posted_line(name, listing, viewer?)` — A listing as one shelf line: price, rating, stock, capacity, offers, counters.
- `$posted_ok(name)` — True while a posted-price market conserves cash and goods.
- `$posted_price(name, listing)` — A listing's asking price now, promotions applied.
- `$shelf(name, item?)` — Listings in stock on a posted-price market, ranked (sponsored, rating, price by default).
