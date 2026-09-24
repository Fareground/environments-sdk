"""The discrete-event engine of the ``operations`` family's ``queue`` mode: one interval at a time, on plain data.

A service system is channels of customers (calls, chats, patients) served by pools of servers (agents, doctors,
counters). Each interval the mode resolves its numbers — expected arrivals, service and patience distributions,
staff on duty — into :class:`Channel` and :class:`Pool` values and calls :func:`run_interval` with the state the
last interval left. The engine plays every arrival, service completion, abandonment, callback and retrial inside
the interval in time order and returns the new state and the counts it produced, keyed by the interval each
customer arrived in (a call arriving at 09:29 and answered at 09:31 counts for 09:00–09:30).

Queue operations are heaps: joining, leaving and serving cost O(log n) however long the line is; a customer who
gave up is dropped lazily when it reaches the head. State between intervals is plain lists (JSON-safe), so the
world keeps it in one journaled property and snapshots, clones and forks carry it unchanged.

Randomness is common across arms: each channel's arrivals in an interval come from a stream of their own (unit-rate
points scaled to the expected count), and each customer's service time, patience and callback choice are drawn in
arrival order from another, by inversion where the distribution allows. Changing staff therefore never changes who
arrives or how long they would take.
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

__all__ = ["Duration", "Channel", "Pool", "Counts", "run_interval", "empty_state", "COUNT_FIELDS"]

#: Per channel and arrival interval: what happened to the customers who arrived in it.
COUNT_FIELDS = ("offered", "answered", "within", "abandoned", "waited", "handle", "callbacks", "callbacks_served",
                "callback_wait", "retrials")
_NORMAL = NormalDist()
#: Keeps a uniform draw inside (0, 1) for inverse distribution functions.
_EDGE = 1e-12


@dataclass(frozen=True)
class Duration:
    """A resolved duration distribution, in the mode's time unit."""

    dist: str
    mean: float
    cv: float = 1.0
    k: int = 2
    low: float = 0.0
    high: float = 0.0

    def draw(self, rng: Any) -> float:
        u = min(1.0 - _EDGE, max(_EDGE, rng.random()))
        if self.dist == "fixed":
            return self.mean
        if self.dist == "exponential":
            return -self.mean * math.log(1.0 - u)
        if self.dist == "lognormal":
            sigma = math.sqrt(math.log(1.0 + self.cv * self.cv))
            return math.exp(math.log(self.mean) - sigma * sigma / 2.0 + sigma * _NORMAL.inv_cdf(u))
        if self.dist == "uniform":
            return self.low + (self.high - self.low) * u
        if self.dist == "erlang":
            total = -math.log(1.0 - u)
            for _ in range(self.k - 1):
                total -= math.log(1.0 - min(1.0 - _EDGE, max(_EDGE, rng.random())))
            return self.mean * total / self.k
        shape = 1.0 / (self.cv * self.cv)  # gamma: the uniforms consumed depend on the shape only, not the mean
        return rng.gammavariate(shape, self.mean / shape) if u else 0.0


@dataclass(frozen=True)
class Channel:
    """A channel's numbers for one interval."""

    name: str
    arrivals: float
    service: Duration
    patience: Duration | None
    priority: float
    threshold: float
    #: ``(offer when the expected wait exceeds, share who accept, servers kept free for live customers)``, or None.
    callback: tuple[float, float, float] | None = None
    #: ``(chance an abandoned customer tries again, delay, most retries)``, or None.
    retry: tuple[float, Duration, int] | None = None


@dataclass(frozen=True)
class Pool:
    """A server pool's numbers for one interval: servers on duty and the channels they serve (first listed first)."""

    name: str
    staff: int
    skills: tuple[str, ...]


@dataclass
class Counts:
    """What one interval produced: counts per arrival interval and channel, and this interval's time-weighted values."""

    #: ``{arrival interval: {channel: {field: value}}}``.
    by_origin: dict[int, dict[str, dict[str, float]]] = field(default_factory=dict)
    #: Time-average customers waiting in the interval, per channel.
    queue: dict[str, float] = field(default_factory=dict)
    max_queue: dict[str, int] = field(default_factory=dict)
    #: Server time spent serving in the interval, per pool (in the mode's unit).
    busy: dict[str, float] = field(default_factory=dict)

    def cell(self, origin: int, channel: str) -> dict[str, float]:
        """The counts of ``channel``'s customers who arrived in interval ``origin`` (created at 0)."""
        per_channel = self.by_origin.get(origin)
        if per_channel is None:
            per_channel = self.by_origin[origin] = {}
        found = per_channel.get(channel)
        if found is None:
            found = per_channel[channel] = dict.fromkeys(COUNT_FIELDS, 0)
        return found


def empty_state() -> dict[str, Any]:
    """The state before the first interval: nobody waiting, nobody served."""
    return {"interval": 0, "seq": 0, "waiting": [], "busy": [], "callbacks": [], "retrials": []}


# A waiting customer: [priority, arrival, seq, channel, deadline (None: never gives up), service, origin, retries].
_PRIORITY, _ARRIVAL, _SEQ, _CHANNEL, _DEADLINE, _SERVICE, _ORIGIN, _RETRIES = range(8)


class _Interval:
    """One interval being played: heaps, counters and the clock."""

    def __init__(self, state: dict[str, Any], index: int, length: float, channels: dict[str, Channel],
                 pools: dict[str, Pool], seeds: Any, name: str):
        self.index, self.name, self.seeds = index, name, seeds
        self.start, self.end = index * length, (index + 1) * length
        self.length = length
        self.channels, self.pools = channels, pools
        self.seq = int(state["seq"])
        self.counts = Counts()
        self.pools_of: dict[str, list[str]] = {c: [p for p, pool in pools.items() if c in pool.skills]
                                               for c in channels}
        self.queues: dict[str, list[tuple[float, float, int]]] = {c: [] for c in channels}
        self.customers: dict[int, list[Any]] = {}
        self.deadlines: list[tuple[float, int]] = []
        for entry in state["waiting"]:
            customer = list(entry)
            self.customers[customer[_SEQ]] = customer
            self.queues[customer[_CHANNEL]].append((-customer[_PRIORITY], customer[_ARRIVAL], customer[_SEQ]))
            if customer[_DEADLINE] is not None:
                self.deadlines.append((customer[_DEADLINE], customer[_SEQ]))
        for heap in self.queues.values():
            heapq.heapify(heap)
        heapq.heapify(self.deadlines)
        self.waiting = {c: len(heap) for c, heap in self.queues.items()}
        self.busy: list[tuple[float, int, str]] = [(float(f), int(s), str(p)) for f, s, p in state["busy"]]
        heapq.heapify(self.busy)
        self.busy_count = {p: 0 for p in pools}
        for _, _, pool in self.busy:
            self.busy_count[pool] = self.busy_count.get(pool, 0) + 1
        self.callbacks: dict[str, deque[list[Any]]] = {c: deque() for c in channels}
        for entry in state["callbacks"]:
            self.callbacks[entry[2]].append(list(entry))
        self.retrials: list[tuple[float, int, str, float, float | None, int]] = [
            (float(t), int(s), str(c), float(sv), None if pt is None else float(pt), int(r))
            for t, s, c, sv, pt, r in state["retrials"]]
        heapq.heapify(self.retrials)
        self.time = self.start
        self.queue_area = {c: 0.0 for c in channels}
        self.busy_area = {p: 0.0 for p in pools}
        self.max_queue = dict(self.waiting)

    # -- the clock ---------------------------------------------------------------------------------------------

    def advance(self, to: float) -> None:
        span = to - self.time
        if span > 0:
            for channel, count in self.waiting.items():
                self.queue_area[channel] += count * span
            for pool, count in self.busy_count.items():
                self.busy_area[pool] += count * span
        self.time = to

    def play(self) -> None:
        arrivals = self._arrivals()
        for pool in self.pools:
            self._pull(pool)
        next_arrival, count = 0, len(arrivals)
        busy, deadlines, retrials, customers = self.busy, self.deadlines, self.retrials, self.customers
        while True:
            while deadlines and deadlines[0][1] not in customers:
                heapq.heappop(deadlines)
            finish = busy[0][0] if busy else math.inf
            deadline = deadlines[0][0] if deadlines else math.inf
            retrial = retrials[0][0] if retrials else math.inf
            arrival = arrivals[next_arrival][0] if next_arrival < count else math.inf
            when = min(finish, deadline, retrial, arrival)
            if when >= self.end:
                break
            self.advance(max(when, self.time))
            if when == finish:  # at equal times: completions, then abandonments, then arrivals
                _, _, pool = heapq.heappop(busy)
                self.busy_count[pool] -= 1
                self._pull(pool)
            elif when == deadline:
                self._give_up(heapq.heappop(deadlines)[1])
            elif when == retrial:
                _, seq, channel, service, patience, retries = heapq.heappop(retrials)
                self.counts.cell(self.index, channel)["retrials"] += 1
                self._arrive(channel, seq, service, patience, 1.0, retries)
            else:
                _, seq, channel, service, patience, callback_u = arrivals[next_arrival]
                next_arrival += 1
                self._arrive(channel, seq, service, patience, callback_u, 0)
        self.advance(self.end)

    # -- arrivals ----------------------------------------------------------------------------------------------

    def _arrivals(self) -> list[tuple[float, int, str, float, float | None, float]]:
        """Every channel's arrivals in the interval, in time order, each with its service time, patience and callback
        draw."""
        out = []
        for name, channel in self.channels.items():
            if channel.arrivals <= 0:
                continue
            points = self.seeds.rng(self.name, "arrivals", name, self.index)
            draws = self.seeds.rng(self.name, "customers", name, self.index)
            total = 0.0
            while True:
                total -= math.log(1.0 - min(1.0 - _EDGE, points.random()))
                if total >= channel.arrivals:
                    break
                self.seq += 1
                service = channel.service.draw(draws)
                patience = channel.patience.draw(draws) if channel.patience is not None else None
                out.append((self.start + self.length * total / channel.arrivals, self.seq, name, service, patience,
                            draws.random()))
        out.sort(key=lambda item: (item[0], item[1]))
        return out

    def _arrive(self, channel: str, seq: int, service: float, patience: float | None, callback_u: float,
                retries: int) -> None:
        spec = self.channels[channel]
        cell = self.counts.cell(self.index, channel)
        cell["offered"] += 1
        for pool in self.pools_of[channel]:
            if self.pools[pool].staff - self.busy_count[pool] > 0:
                cell["answered"] += 1
                cell["within"] += 1
                cell["handle"] += service
                self._start(pool, service, seq)
                return
        if (spec.callback is not None and callback_u < spec.callback[1] and self._expected_wait(channel)
            > spec.callback[0]):
            cell["callbacks"] += 1
            self.callbacks[channel].append([self.time, seq, channel, service, self.index])
            return
        deadline = None if patience is None else self.time + patience
        customer = [spec.priority, self.time, seq, channel, deadline, service, self.index, retries]
        self.customers[seq] = customer
        heapq.heappush(self.queues[channel], (-spec.priority, self.time, seq))
        self.waiting[channel] += 1
        self.max_queue[channel] = max(self.max_queue[channel], self.waiting[channel])
        if deadline is not None:
            heapq.heappush(self.deadlines, (deadline, seq))

    def _reserve(self, channel: str) -> float:
        """Servers a pool keeps free for live customers before it serves one of ``channel``'s callbacks."""
        callback = self.channels[channel].callback
        return callback[2] if callback is not None else 0.0

    def _expected_wait(self, channel: str) -> float:
        """How long a new customer would wait: everyone in line ahead of them served at the channel's mean pace."""
        servers = sum(self.pools[pool].staff for pool in self.pools_of[channel])
        return (self.waiting[channel] + 1) * self.channels[channel].service.mean / max(1, servers)

    # -- service -----------------------------------------------------------------------------------------------

    def _start(self, pool: str, service: float, seq: int) -> None:
        heapq.heappush(self.busy, (self.time + service, seq, pool))
        self.busy_count[pool] += 1

    def _pull(self, pool: str) -> None:
        """Free servers of ``pool`` take the waiting customer first in line (priority, then arrival); with nobody
        waiting on its channels, the oldest callback."""
        spec = self.pools[pool]
        while spec.staff - self.busy_count[pool] > 0:
            best: tuple[tuple[float, float, int], str] | None = None
            for channel in spec.skills:
                heap = self.queues.get(channel)
                self._drop_given_up(channel)
                if heap and (best is None or heap[0] < best[0]):
                    best = (heap[0], channel)
            if best is not None:
                heapq.heappop(self.queues[best[1]])
                customer = self.customers.pop(best[0][2])
                self.waiting[best[1]] -= 1
                wait = self.time - customer[_ARRIVAL]
                origin, threshold = int(customer[_ORIGIN]), self.channels[best[1]].threshold
                cell = self.counts.cell(origin, best[1])
                cell["answered"] += 1
                cell["waited"] += wait
                cell["handle"] += customer[_SERVICE]
                if wait <= threshold:
                    cell["within"] += 1
                self._start(pool, customer[_SERVICE], int(customer[_SEQ]))
                continue
            free = spec.staff - self.busy_count[pool]
            oldest = min((line[0] for c in spec.skills for line in [self.callbacks.get(c)]
                          if line and free > self._reserve(c)), key=lambda entry: (entry[0], entry[1]), default=None)
            if oldest is None:
                return
            self.callbacks[oldest[2]].popleft()
            cell = self.counts.cell(int(oldest[4]), oldest[2])
            cell["callbacks_served"] += 1
            cell["handle"] += oldest[3]
            cell["callback_wait"] += self.time - oldest[0]
            self._start(pool, oldest[3], int(oldest[1]))

    def _drop_given_up(self, channel: str) -> None:
        heap = self.queues.get(channel)
        while heap and heap[0][2] not in self.customers:
            heapq.heappop(heap)

    def _give_up(self, seq: int) -> None:
        customer = self.customers.pop(seq, None)
        if customer is None:
            return
        channel = customer[_CHANNEL]
        self.waiting[channel] -= 1
        self.counts.cell(int(customer[_ORIGIN]), channel)["abandoned"] += 1
        retry = self.channels[channel].retry
        retries = int(customer[_RETRIES])
        if retry is None or retries >= retry[2]:
            return
        draws = self.seeds.rng(self.name, "retry", seq, retries)
        if draws.random() >= retry[0]:
            return
        patience = None if customer[_DEADLINE] is None else customer[_DEADLINE] - customer[_ARRIVAL]
        heapq.heappush(self.retrials, (self.time + retry[1].draw(draws), seq, channel, customer[_SERVICE], patience,
                                       retries + 1))

    # -- the state the next interval starts from ------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        waiting = sorted(self.customers.values(), key=lambda c: (c[_ARRIVAL], c[_SEQ]))
        callbacks = sorted((entry for line in self.callbacks.values() for entry in line), key=lambda e: (e[0], e[1]))
        return {"interval": self.index + 1, "seq": self.seq, "waiting": waiting,
                "busy": [list(item) for item in sorted(self.busy)], "callbacks": callbacks,
                "retrials": [list(item) for item in sorted(self.retrials)]}


def run_interval(state: dict[str, Any], length: float, channels: dict[str, Channel], pools: dict[str, Pool],
                 seeds: Any, name: str) -> tuple[dict[str, Any], Counts]:
    """Play interval ``state["interval"]`` (``length`` long) and return the state after it and what it produced."""
    played = _Interval(state, int(state["interval"]), length, channels, pools, seeds, name)
    played.play()
    counts = played.counts
    counts.queue = {c: area / length for c, area in played.queue_area.items()}
    counts.max_queue = dict(played.max_queue)
    counts.busy = dict(played.busy_area)
    return played.state(), counts
