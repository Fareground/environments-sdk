# agreements / bookings

### `agreements.bookings`
Capacity-limited places: with `format: slots` guests book a future round (paying on booking) and join a waitlist when it is full, promoted first-fit when a place frees; with `format: queue` they wait in line and are served (paying on service) as capacity allows each round. Service is FIFO or by `priority`, and waiting guests give up after `patience` rounds. Generates `<name>_book` and `<name>_cancel`; resources are entities of `<name>_resource` (served, offered, revenue), bookings of `<name>_booking`. Totals in $world.<name>_stats; utilization is stats.served / stats.offered.

Config:
- `who` (required): Type(s) that book.
- `resources` (default {}): {resource id: {provider, capacity, price, horizon, max_party}}.
- `format` (default "slots"): slots: book a future round; queue: wait in line, served as places free up.
- `currency` (default null): Ledger currency for prices.
- `waitlist` (default true): Slots format: a full round puts the guest on its waitlist instead of refusing.
- `order` (default "fifo"): Who is served or promoted first.
- `priority` (default null): Priority order: expression over $it (the guest), higher first.
- `patience` (default null): Rounds a waiting guest waits before giving up (number or expression over $it).
- `refund` (default 1): Share of the price returned when a booking is cancelled.
- `actions` (default ["book", "cancel"]): Tools generated for agent guests.

Nested config:
**ResourceSpec** — Something with limited places each round.
- `provider`: text — Entity id paid for bookings (needed when price > 0).
- `name`: text
- `capacity`: int | text = 1 — Places per round (number or expression over $inputs).
- `price`: number = 0 — Price per place.
- `horizon`: int = 3 — Slots format: how many rounds ahead guests may book.
- `max_party`: int = 1 — Most places one booking may take.
- `description`: text

Actions of the `agreements` op:
- `book` — takes `who`, `resource`, `ahead`, `party` (needs `who`, `resource`): {"agreements": "dining", "action": "book", "who": "$actor", "resource": "$params.resource", "ahead": 2, "party": 4}  (book places, or join the waitlist or the line; `ahead` only with format slots)
- `cancel` — takes `booking` (needs `booking`): {"agreements": "dining", "action": "cancel", "booking": "$params.booking"}  (cancel with a refund; the waitlist moves up)

```json
{"mechanisms": {"my_bookings": {"kind": "agreements", "mode": "bookings", "who": "household", "currency": "cash", "waitlist": true, "patience": 2, "resources": {"tables": {"provider": "bistro", "capacity": 8, "price": 25, "horizon": 3, "max_party": 4}}}}}
```
