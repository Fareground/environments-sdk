# Auction house

An auction house sells six paintings to six collectors, each an AI agent with $300 of cash and a private opinion of what each painting is worth to them. Lots 1 to 3 are sold by open ascending auction: collectors take turns raising the current high bid by at least $5, everyone can see the current high bid, and the lot closes once a full round passes with no new bid. Lots 4 to 6 are sold by sealed bid: every collector privately submits one bid and the highest bid wins and pays what it bid. If two sealed bids tie, the one submitted first wins. A collector can never bid more cash than it has left. Winners pay the house, and every painting is sold at most once. The house wants to know its revenue and who bought what.

## Deliverables

Report these outputs:
- `lots` — a list with one entry per lot, each a map with `lot` (1 to 6), `format` ("english" or "sealed"), `winner` (the collector's id, or null if unsold) and `price` (null if unsold).
- `house_revenue` — the total the house received.
- `cash_start_total` — the collectors' total cash at the start.
- `cash_end_total` — the collectors' total cash at the end.
