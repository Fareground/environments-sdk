# market / posted

### `market.posted`
A market of listings at posted prices with stock, per-round capacity, promotions, sponsored placement, ratings and haggling (an offer at or above the floor is accepted, below it the seller counters at the floor). Buyer tools `<name>_buy`, `<name>_offer`, `<name>_accept`, `<name>_rate`; seller tools `<name>_set_price`, `<name>_promote`, `<name>_sponsor`. Listings are `<name>_listing` entities; goods land in the buyer's `<name>_basket` ({item: count}). Read the ranked shelf with $shelf(name, item?), prices with $posted_price(name, listing).

Config:
- `who` (required): Agent type that shops (subtypes included).
- `sellers` (default null): Agent type that manages its listings (sets prices, promotes, sponsors).
- `currency` (default "cash"): Property holding money (buyers and sellers).
- `listings` (default {}): Listings by id.
- `rank` (default ["sponsored", "rating", "price"]): Shelf order: sponsored first, higher rating, lower price, more sold.
- `shelf` (default 6): Listings shown on the shelf view.
- `max_qty` (default 10): Units per purchase.
- `counter_rounds` (default 1): Rounds a counter-offer stays open.
- `ratings` (default true): Buyers may rate listings they bought from (once each).
- `sponsor_fee` (default 0): Fee per round of sponsored placement, paid to $world.<name>_ad_revenue.
- `max_promo` (default 0.5): Largest promotion discount a seller may run.
- `stage` (default null): Trade during this declared stage; default: a sequential stage named after the market.
- `max_actions` (default 3): Actions per turn in the generated stage.
- `conserve` (default true): Declare the invariant that no listing's stock goes negative.

Nested config:
**ListingSpec** — One listing declared with the market.
- `seller`: text (required) — Entity id of the seller (paid on every sale).
- `item`: text (required) — What is sold; baskets count goods by item.
- `price`: number (required) — Asking price per unit.
- `stock`: int = 0 — Units available.
- `capacity`: int = 0 — Units that can be sold per round (0 = no limit).
- `negotiable`: bool = false — Buyers may make offers below the price.
- `floor`: number — Lowest price the seller accepts in haggling (default: the price).
- `sponsored`: bool = false — Placed first on the shelf.
- `rating`: number = 0 — Starting average rating (0 = unrated).
- `ratings`: int = 0 — Ratings behind the starting average.
- `name`: text — Display name (default: the item).

Actions of the `market` op:
- `buy` — takes `who`, `listing`, `qty` (needs `listing`, `qty`): {"market": "market", "action": "buy", "listing": "$params.listing", "qty": 2}  (buy from a listing at its current price)
- `offer` — takes `who`, `listing`, `price` (needs `listing`, `price`): {"market": "market", "action": "offer", "listing": "$params.listing", "price": 2.5}  (offer less than the asking price for one unit of a negotiable listing)
- `accept` — takes `who`, `listing` (needs `listing`): {"market": "market", "action": "accept", "listing": "$params.listing"}  (buy one unit at the seller's open counter-offer)
- `rate` — takes `who`, `listing`, `stars` (needs `listing`, `stars`): {"market": "market", "action": "rate", "listing": "$params.listing", "stars": 5}  (rate a listing bought from, once)
- `set_price` — takes `who`, `listing`, `price` (needs `listing`, `price`): {"market": "market", "action": "set_price", "listing": "$params.listing", "price": 4}  (a seller changes its listing's price)
- `promote` — takes `who`, `listing`, `pct`, `rounds` (needs `listing`, `pct`, `rounds`): {"market": "market", "action": "promote", "listing": "$params.listing", "pct": 0.2, "rounds": 3}  (a seller runs a discount)
- `sponsor` — takes `who`, `listing`, `rounds` (needs `listing`, `rounds`): {"market": "market", "action": "sponsor", "listing": "$params.listing", "rounds": 2}  (a seller puts its listing first on the shelf)

```json
{"types": {"farmer": {"agent": true}, "shopper": {"agent": true, "props": {"cash": 100}}}, "entities": {"ana": {"type": "farmer"}, "shopper": {"type": "shopper", "count": 2}}, "mechanisms": {"my_posted": {"kind": "market", "mode": "posted", "who": "shopper", "sellers": "farmer", "listings": {"apples": {"seller": "ana", "item": "apples", "price": 3, "stock": 40, "negotiable": true, "floor": 2.5}}}}}
```
