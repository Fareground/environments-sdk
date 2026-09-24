"""Winner determination for package (combinatorial) auctions: exact branch and bound, VCG payments.

Bidders bid on bundles of items; each bidder wins at most one of its bids (XOR bids) and no item is
sold twice. Reserve prices are per item: a bid's *surplus* is its price minus the reserves of its
items, and the seller keeps an item nobody values above its reserve. The winning allocation
maximises total surplus, found exactly by a depth-first branch and bound over bids ordered by surplus
(highest first, then as given); among allocations with equal surplus the first one found wins, which
favours higher and then earlier bids. Winner determination is NP-hard, so a search is capped at
:data:`SEARCH_BUDGET` steps and a lot at :data:`MAX_PACKAGE_BIDS` bids; beyond them it is an error,
never a silently worse answer.

VCG payments: a winner pays its price minus the surplus it adds — the best surplus the others reach
without any of its bids, subtracted from what they get alongside it. That makes bidding true values
safe, never charges less than the reserves of the items won, nor more than the bid.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..expr import Call, _describe, charge, function
from ..stdlib._args import fail, list_arg, map_arg

__all__ = ["PackageBid", "SearchLimit", "best_allocation", "settle", "MAX_PACKAGE_BIDS", "MAX_PACKAGE_ITEMS",
           "SEARCH_BUDGET", "PAYMENTS"]

#: Most bids one lot may hold.
MAX_PACKAGE_BIDS = 64
#: Most distinct items one lot may sell.
MAX_PACKAGE_ITEMS = 24
#: Most search steps (nodes visited) settling one lot, VCG re-solves included.
SEARCH_BUDGET = 400_000
PAYMENTS = ("vcg", "pay_bid")
_EPS = 1e-9


@dataclass(frozen=True)
class PackageBid:
    """One bid: a bidder, the items it wants together, and its price for all of them."""

    bidder: str
    items: tuple[str, ...]
    price: float


class SearchLimit(Exception):
    """Winner determination needed more steps than its budget."""


class _Steps:
    """Search steps left for one lot; each step is also reported to ``on_step`` (the expression work budget)."""

    def __init__(self, budget: int, on_step: Callable[[int], None] | None):
        self.budget, self.used, self.on_step = budget, 0, on_step

    def take(self, count: int) -> None:
        self.used += count
        if self.on_step is not None:
            self.on_step(count)
        if self.used > self.budget:
            raise SearchLimit(f"winner determination needed more than {self.budget:,} search steps")


def _reserve(items: Sequence[str], reserves: Mapping[str, float]) -> float:
    return sum(reserves.get(item, 0.0) for item in items)


def best_allocation(bids: Sequence[PackageBid], reserves: Mapping[str, float], skip: str | None = None,
                    steps: _Steps | None = None) -> tuple[list[int], float]:
    """Indices of the winning bids (in ``bids``) and their total surplus, ignoring bids by ``skip``."""
    counter = steps or _Steps(SEARCH_BUDGET, None)
    bits = {item: 1 << bit for bit, item in enumerate(sorted({item for bid in bids for item in bid.items}))}
    candidates = []
    for index, bid in enumerate(bids):
        surplus = bid.price - _reserve(bid.items, reserves)
        if bid.bidder != skip and surplus >= -_EPS:
            mask = 0
            for item in bid.items:
                mask |= bits[item]
            candidates.append((index, max(surplus, 0.0), mask, bid.bidder))
    candidates.sort(key=lambda c: (-c[1], c[0]))
    suffix = [0.0] * (len(candidates) + 1)
    for position in range(len(candidates) - 1, -1, -1):
        suffix[position] = suffix[position + 1] + candidates[position][1]
    best: list[Any] = [-1.0, []]
    chosen: list[int] = []

    def search(position: int, taken: int, bidders: frozenset, total: float) -> None:
        counter.take(1)
        if position == len(candidates):
            if total > best[0] + _EPS:
                best[0], best[1] = total, list(chosen)
            return
        if total + suffix[position] <= best[0] + _EPS:
            return  # cannot beat the best found so far (an equal total found later never replaces it)
        index, surplus, mask, bidder = candidates[position]
        if not taken & mask and bidder not in bidders:
            chosen.append(index)
            search(position + 1, taken | mask, bidders | {bidder}, total + surplus)
            chosen.pop()
        search(position + 1, taken, bidders, total)

    search(0, 0, frozenset(), 0.0)
    return sorted(best[1]), max(best[0], 0.0)


def settle(bids: Sequence[PackageBid], reserves: Mapping[str, float], payment: str = "vcg",
           budget: int = SEARCH_BUDGET,
           on_step: Callable[[int], None] | None = None) -> tuple[list[dict[str, Any]], float]:
    """The winners ``[{bid, bidder, items, price, pays}]`` (in bid order) and the total surplus.

    Raises :class:`SearchLimit` when the search needs more than ``budget`` steps in all."""
    if payment not in PAYMENTS:
        raise ValueError(f"payment must be one of {', '.join(PAYMENTS)}, got {payment!r}")
    steps = _Steps(budget, on_step)
    chosen, total = best_allocation(bids, reserves, steps=steps)
    winners = []
    for index in chosen:
        bid = bids[index]
        floor = _reserve(bid.items, reserves)
        if payment == "pay_bid":
            pays = bid.price
        else:
            _, without = best_allocation(bids, reserves, skip=bid.bidder, steps=steps)
            pays = bid.price - (total - without)
        winners.append({"bid": index, "bidder": bid.bidder, "items": list(bid.items), "price": bid.price,
                        "pays": round(min(bid.price, max(floor, pays)), 10)})
    return winners, total


# ---------------------------------------------------------------------------
# $package_winners
# ---------------------------------------------------------------------------


def _bids_arg(call: Call) -> list[PackageBid]:
    raw = list_arg(call, 0, "a list of bids like {bidder, items, price}")
    if len(raw) > MAX_PACKAGE_BIDS:
        raise fail(call, f"{len(raw)} bids; winner determination takes at most {MAX_PACKAGE_BIDS}")
    bids = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict) or not {"bidder", "items", "price"} <= set(entry):
            raise fail(call, f"bid {position} must be a map with bidder, items and price, got {_describe(entry)}")
        bidder, items, price = entry["bidder"], entry["items"], entry["price"]
        bidder = getattr(bidder, "id", bidder)
        if not isinstance(bidder, str) or not bidder:
            raise fail(call, f"bid {position}: bidder must be an entity or text, got {_describe(entry['bidder'])}")
        if (not isinstance(items, list) or not items or not all(isinstance(i, str) for i in items) or len(set(items))
            != len(items)):
            raise fail(call,
                       f"bid {position}: items must be a non-empty list of distinct item names, got {_describe(items)}")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
            raise fail(call, f"bid {position}: price must be a number ≥ 0, got {_describe(price)}")
        bids.append(PackageBid(str(bidder), tuple(items), float(price)))
    if len({item for bid in bids for item in bid.items}) > MAX_PACKAGE_ITEMS:
        raise fail(call, f"the bids name more than {MAX_PACKAGE_ITEMS} distinct items")
    return bids


def _reserves_arg(call: Call, bids: Sequence[PackageBid]) -> dict[str, float]:
    if len(call) < 2 or call.arg(1) is None:
        return {}
    value = call.arg(1)
    items = {item for bid in bids for item in bid.items}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {item: float(value) for item in items}
    reserves = map_arg(call, 1)
    for item, amount in reserves.items():
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            raise fail(call, f"the reserve of {item!r} must be a number ≥ 0, got {_describe(amount)}")
    return {str(item): float(amount) for item, amount in reserves.items()}


@function("package_winners(bids, reserves?, payment?)",
          "Exact winner determination for package bids [{bidder, items, price}] (each bidder wins at most one bid, "
          "no item twice): {winners: [{bidder, items, price, pays}], surplus, revenue}. `reserves` is one number "
          "per item or {item: reserve}; `payment` vcg (default: winners pay the surplus they displace) or pay_bid.",
          min_args=1, max_args=3)
def _package_winners(call: Call) -> dict[str, Any]:
    bids = _bids_arg(call)
    reserves = _reserves_arg(call, bids)
    payment = call.arg(2) if len(call) > 2 and call.arg(2) is not None else "vcg"
    if payment not in PAYMENTS:
        raise fail(call, f"payment must be one of {', '.join(PAYMENTS)}, got {_describe(payment)}")
    try:
        winners, surplus = settle(bids, reserves, payment, on_step=lambda used: charge(used, call.source))
    except SearchLimit as exc:
        raise fail(call, f"{exc}; bid on fewer packages") from None
    return {"winners": [{key: w[key] for key in ("bidder", "items", "price", "pays")} for w in winners],
            "surplus": round(surplus, 10), "revenue": round(sum(w["pays"] for w in winners), 10)}
