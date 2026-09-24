"""Per-interval statistics of the ``operations.queue`` mode: what each interval's customers met, and the run's totals.

Counts belong to the interval a customer arrived in, so an interval's record keeps changing while its customers are
still waiting; time-weighted values (queue length, server time, staff, cost) belong to the interval they were
measured in. Records are replaced, never changed in place, so the world's journal and every copy of a run see a
record either before or after an interval, never half-way.

Rates use the customers who joined the line — everyone offered, less those who took a callback instead:
service level is the share answered within the channel's threshold, abandonment the share who gave up, and the
average speed of answer is the mean wait of those answered. Utilisation is server time spent serving over server
time on duty.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ops_engine import COUNT_FIELDS, Counts

__all__ = ["record_for", "merge_counts", "empty_totals", "updated_totals", "latest", "RATE_FIELDS"]

#: Rates every record and total carries, recomputed from its counts.
RATE_FIELDS = ("service_level", "asa", "abandon_rate")


def _rates(cell: dict[str, Any]) -> None:
    joined = cell["offered"] - cell["callbacks"]
    cell["service_level"] = cell["within"] / joined if joined > 0 else None
    cell["abandon_rate"] = cell["abandoned"] / joined if joined > 0 else None
    cell["asa"] = cell["waited"] / cell["answered"] if cell["answered"] > 0 else None
    handled = cell["answered"] + cell["callbacks_served"]
    cell["aht"] = cell["handle"] / handled if handled > 0 else None


def _zero_counts() -> dict[str, Any]:
    cell: dict[str, Any] = dict.fromkeys(COUNT_FIELDS, 0)
    _rates(cell)
    return cell


def record_for(index: int, start: float, length: float, hours: float, now: Mapping[str, Any], counts: Counts,
               targets: Mapping[str, float | None]) -> dict[str, Any]:
    """The record of the interval just played, before counts are merged into it."""
    channels = {}
    for name, numbers in now["channels"].items():
        cell = _zero_counts()
        cell.update(expected=numbers["arrivals"], queue=counts.queue.get(name, 0.0),
                    max_queue=counts.max_queue.get(name, 0))
        channels[name] = cell
    pools = {}
    for name, numbers in now["pools"].items():
        staff = numbers["staff"]
        busy = counts.busy.get(name, 0.0)
        paid = staff * hours / (1.0 - numbers["shrinkage"])
        pools[name] = {"staff": staff, "busy": busy,
                       "utilisation": min(1.0, busy / (staff * length)) if staff else None,
                       "paid_hours": paid, "cost": paid * numbers["cost"]}
    record: dict[str, Any] = {"interval": index, "start": start, **_zero_counts(),
                              "expected": sum(c["expected"] for c in channels.values()),
                              "queue": sum(c["queue"] for c in channels.values()),
                              "max_queue": sum(c["max_queue"] for c in channels.values()),
                              "staff": sum(p["staff"] for p in pools.values()),
                              "busy": sum(p["busy"] for p in pools.values()),
                              "paid_hours": sum(p["paid_hours"] for p in pools.values()),
                              "cost": sum(p["cost"] for p in pools.values()),
                              "channels": channels, "pools": pools, "below_target": False}
    staffed = record["staff"] * length
    record["utilisation"] = min(1.0, record["busy"] / staffed) if staffed else None
    return _finish(record, targets)


def _finish(record: dict[str, Any], targets: Mapping[str, float | None]) -> dict[str, Any]:
    for cell in record["channels"].values():
        _rates(cell)
    _rates(record)
    record["below_target"] = any(
        target is not None and cell["service_level"] is not None and cell["service_level"] < target
        for name, cell in record["channels"].items() for target in [targets.get(name)])
    return record


def merge_counts(records: list[dict[str, Any]], counts: Counts, targets: Mapping[str, float | None]
                 ) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """``records`` with the counts added to the intervals they belong to (new record objects for those), and the
    ``(before, after)`` pair of every record that changed."""
    out = list(records)
    changed = []
    for origin, per_channel in sorted(counts.by_origin.items()):
        before = out[origin]
        after = {**before, "channels": {name: dict(cell) for name, cell in before["channels"].items()}}
        for channel, delta in per_channel.items():
            cell = after["channels"][channel]
            for key, amount in delta.items():
                cell[key] += amount
                after[key] += amount
        out[origin] = _finish(after, targets)
        changed.append((before, out[origin]))
    return out, changed


def empty_totals(channels: list[str]) -> dict[str, Any]:
    """Totals before the first interval (every key present, so expressions reading them check cleanly)."""
    return {**_zero_counts(), "channels": {name: _zero_counts() for name in channels}, "intervals": 0,
            "intervals_below_target": 0, "staff_time": 0.0, "busy": 0.0, "paid_hours": 0.0, "cost": 0.0,
            "utilisation": None, "waiting": 0, "callbacks_waiting": 0,
            "latest": {"interval": None, "service_level": None, "offered": 0, "abandon_rate": None, "staff": 0,
                       "queue": 0.0}}


def updated_totals(totals: Mapping[str, Any], new: dict[str, Any], changed: list[tuple[dict[str, Any], dict[str, Any]]],
                   length: float, state: Mapping[str, Any]) -> dict[str, Any]:
    """Totals after an interval: ``new`` is its record as first created (before counts), ``changed`` every record
    the interval's counts touched."""
    out = {**totals, "channels": {name: dict(cell) for name, cell in totals["channels"].items()}}
    out["intervals"] += 1
    out["staff_time"] += new["staff"] * length
    for key in ("busy", "paid_hours", "cost"):
        out[key] += new[key]
    below = out["intervals_below_target"] + (1 if new["below_target"] else 0)
    for before, after in changed:
        for key in COUNT_FIELDS:
            out[key] += after[key] - before[key]
        for name, cell in after["channels"].items():
            for key in COUNT_FIELDS:
                out["channels"][name][key] += cell[key] - before["channels"][name][key]
        below += int(after["below_target"]) - int(before["below_target"])
    out["intervals_below_target"] = below
    for cell in out["channels"].values():
        _rates(cell)
    _rates(out)
    out["utilisation"] = out["busy"] / out["staff_time"] if out["staff_time"] else None
    out["waiting"] = len(state["waiting"])
    out["callbacks_waiting"] = len(state["callbacks"])
    return out


def latest(totals: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Totals naming the latest interval played (what the mode's metrics read each round)."""
    return {**totals, "latest": {"interval": record["interval"], "service_level": record["service_level"],
                                 "offered": record["offered"], "abandon_rate": record["abandon_rate"],
                                 "staff": record["staff"], "queue": record["queue"]}}
