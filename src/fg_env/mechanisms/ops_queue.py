"""The ``economy`` family's ``queue`` mode: customers arriving on channels, served by staffed server pools.

A contact centre, a clinic, a counter or a repair crew: each interval (a round, or a stretch of a continuous clock)
the mode reads its numbers — expected arrivals per channel, service and patience distributions, staff on duty per
pool — and plays every arrival, answer, abandonment, callback and retrial natively (:mod:`.ops_engine`). The results
are per-interval records and running totals in world props (:mod:`.ops_stats`), read by generated outputs and
metrics. Numbers are expressions, so staffing is an input vector an optimiser can search
(``"staff": "$inputs.staffing[$interval]"``), arrivals follow patterns (``"$pattern.calls * $pattern.outage"``) and
agents can change what the next interval reads (``"$world.rostered"``).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field

from ..errors import RunError
from ..expr import ExprError, compile_expr
from ..registry import MechanismError, family_action, mode, parsed
from ._common import Config, Number, suggest
from .econ_base import compiles, valid_name
from .ops_engine import Channel, Duration, Pool, empty_state, run_interval
from .ops_stats import empty_totals, latest, merge_counts, record_for, updated_totals

__all__ = ["QueueConfig", "ChannelSpec", "PoolSpec", "DurationSpec", "interval_length"]

KEY = "economy.queue"
#: Seconds in each time unit the mode and a clock may use.
UNIT_SECONDS = {"second": 1.0, "minute": 60.0, "hour": 3600.0, "day": 86400.0, "week": 604800.0}


class DurationSpec(Config):
    """How long something takes, in the mode's `unit`."""

    dist: Literal["exponential", "lognormal", "gamma", "erlang", "fixed", "uniform"] = Field(
        "exponential", description="exponential (memoryless: Erlang C and A assume it) | lognormal and gamma (mean "
                                   "and cv) | erlang (k phases) | fixed | uniform (low to high).")
    mean: Number = Field(..., description="Mean duration (number or expression; not used by uniform).")
    cv: Number = Field(1.0, description="lognormal, gamma: coefficient of variation (sd ÷ mean).")
    k: int = Field(2, ge=1, description="erlang: phases (cv = 1/√k).")
    low: Number = Field(0.0, description="uniform: shortest.")
    high: Number = Field(0.0, description="uniform: longest.")


class CallbackSpec(Config):
    """A callback offered to customers facing a long wait; callbacks are served when nobody is waiting."""

    when: Number = Field(0.0, description="Offer it when the expected wait is longer than this (the mode's unit): "
                                          "(customers waiting on the channel + 1) × mean service ÷ servers on the "
                                          "channel.")
    accept: Number = Field(1.0, description="Share of customers offered a callback who take it, from 0 to 1.")
    reserve: Number = Field(0.0, description="Servers kept free for live customers: a callback is served only while "
                                             "more than this many servers of the pool are free (0: whenever nobody "
                                             "is waiting, which can take the server the next caller needed).")


class RetrySpec(Config):
    """Customers who gave up trying again later."""

    chance: Number = Field(..., description="Chance a customer who gave up tries again, from 0 to 1.")
    delay: DurationSpec = Field(..., description="How long after giving up they try again.")
    max: int = Field(1, ge=1, description="Most retries per customer.")


class ChannelSpec(Config):
    """A kind of customer: calls, chats, emails, walk-ins."""

    arrivals: Number = Field(..., description="Expected arrivals in the interval (number or expression over "
                                              "$interval, $inputs, $world, $pattern); arrivals are a Poisson process "
                                              "at that rate.")
    service: DurationSpec = Field(..., description="Service (handle) time.")
    patience: DurationSpec | None = Field(None, description="How long a customer waits before giving up (null: never).")
    priority: int = Field(0, description="Higher is served first; equal priorities are served in arrival order.")
    threshold: float = Field(20.0, ge=0,
                             description="Service level threshold: answered within this long (the mode's unit).")
    target: float | None = Field(None, ge=0, le=1, description="Service level the channel aims for; an interval below "
                                                               "it counts in <name>_intervals_below_target.")
    callback: CallbackSpec | None = Field(None, description="Offer callbacks to customers facing a long wait.")
    retry: RetrySpec | None = Field(None, description="Customers who gave up try again.")
    description: str = ""


class PoolSpec(Config):
    """Servers with the same skills: agents, doctors, counters, technicians."""

    staff: Number = Field(..., description="Servers on duty in the interval: a whole number or an expression giving "
                                           "one ($inputs.staffing[$interval]); a shift is staff that changes by "
                                           "interval.")
    skills: list[str] | Literal["all"] = Field("all", description="Channels the pool serves, most preferred first (a "
                                                                  "free server takes the waiting customer first by "
                                                                  "priority, then arrival).")
    cost: Number = Field(0.0, description="Cost of one server per paid hour.")
    shrinkage: Number = Field(0.0, description="Share of paid time not on duty (breaks, training), from 0 to below 1: "
                                               "paid hours = staff × hours ÷ (1 − shrinkage).")
    description: str = ""


class QueueConfig(Config):
    """A service system: channels of customers served by pools of servers, interval by interval."""

    channels: dict[str, ChannelSpec] = Field(..., description="{channel: {arrivals, service, patience, priority, "
                                                              "threshold, target, callback, retry}}.")
    servers: dict[str, PoolSpec] = Field(..., description="{pool: {staff, skills, cost, shrinkage}}.")
    unit: Literal["second", "minute", "hour"] = Field("second", description="Unit of every duration and threshold.")
    interval: float | None = Field(None, gt=0, description="Length of one interval in `unit` (default: one round of a "
                                                           "clock whose unit is second, minute, hour, day or week). "
                                                           "Needed on a continuous clock and on rounds without a time "
                                                           "unit.")


def _clock_unit(clock: Mapping[str, Any]) -> str:
    return str(clock.get("unit") or "round").lower().rstrip("s")


def interval_length(config: QueueConfig, clock: Mapping[str, Any]) -> tuple[float, float | None]:
    """``(interval length in the mode's unit, the same in clock units on a continuous clock else None)``; raises
    :class:`MechanismError` when the clock does not give one."""
    unit = _clock_unit(clock)
    continuous = clock.get("mode") == "continuous"
    if config.interval is not None:
        length = float(config.interval)
    elif not continuous and unit in UNIT_SECONDS:
        length = int(clock.get("step") or 1) * UNIT_SECONDS[unit] / UNIT_SECONDS[config.unit]
    else:
        raise MechanismError("needs `interval`: how long one interval is, in `unit`",
                             "e.g. \"unit\": \"second\", \"interval\": 1800 for half-hours (or a clock whose unit is "
                             "second, minute, hour, day or week: each round is then one interval)", "interval")
    if not continuous:
        return length, None
    if unit not in UNIT_SECONDS:
        raise MechanismError(f"a continuous clock in '{clock.get('unit')}' has no length in {config.unit}s",
                             "give the clock a time unit: second, minute, hour or day", "interval")
    return length, length * UNIT_SECONDS[config.unit] / UNIT_SECONDS[unit]


def _check(config: QueueConfig) -> None:
    if not config.channels:
        raise MechanismError("needs at least one channel", "e.g. \"channels\": {\"calls\": {\"arrivals\": 120, "
                                                           "\"service\": {\"mean\": 300}}}", "channels")
    if not config.servers:
        raise MechanismError("needs at least one server pool", "e.g. \"servers\": {\"agents\": {\"staff\": 12}}",
                             "servers")
    for name, channel in config.channels.items():
        path = f"channels.{name}"
        if not valid_name(name):
            raise MechanismError(f"channel '{name}' is not a valid name", "use letters, digits and _", path)
        durations = [("service", channel.service), ("patience", channel.patience),
                     ("retry.delay", channel.retry.delay if channel.retry else None)]
        for field, spec in durations:
            if spec is not None:
                _check_duration(spec, f"{path}.{field}")
        for field, value in (("arrivals", channel.arrivals),
                             ("callback.when", channel.callback and channel.callback.when),
                             ("callback.accept", channel.callback and channel.callback.accept),
                             ("callback.reserve", channel.callback and channel.callback.reserve),
                             ("retry.chance", channel.retry and channel.retry.chance)):
            compiles(value, f"{path}.{field}")
    served = set()
    for name, pool in config.servers.items():
        path = f"servers.{name}"
        if not valid_name(name):
            raise MechanismError(f"server pool '{name}' is not a valid name", "use letters, digits and _", path)
        for skill in pool.skills if isinstance(pool.skills, list) else []:
            if skill not in config.channels:
                raise MechanismError(f"'{skill}' is not a channel", suggest(skill, config.channels), f"{path}.skills")
        served |= set(config.channels if pool.skills == "all" else pool.skills)
        for field in ("staff", "cost", "shrinkage"):
            compiles(getattr(pool, field), f"{path}.{field}")
    unserved = [name for name in config.channels if name not in served]
    if unserved:
        raise MechanismError(f"no server pool serves {', '.join(unserved)}",
                             "add the channel to a pool's `skills` (or leave `skills` out: all channels)", "servers")


def _check_duration(spec: DurationSpec, path: str) -> None:
    for field in ("mean", "cv", "low", "high"):
        compiles(getattr(spec, field), f"{path}.{field}")
    if spec.dist == "uniform" and spec.low == spec.high == 0.0:
        raise MechanismError("a uniform duration needs `low` and `high`", "e.g. {\"dist\": \"uniform\", \"low\": 60, "
                                                                          "\"high\": 240, \"mean\": 150}", path)


@mode("economy", "queue", QueueConfig,
      "A service system played natively, interval by interval: customers arrive on each channel (a Poisson process at "
      "the interval's expected `arrivals`), are answered at once by a free server of a pool with the skill, or wait in "
      "line — by `priority`, then arrival — and give up when their `patience` runs out; `callback` offers customers "
      "facing a long wait a call back, served when nobody is waiting, and `retry` brings some who gave up back later. "
      "Servers finish what they started when staff drops. Every number is read when the interval is played (on a "
      "continuous clock: when it starts) and may read `$interval` (0 for the first), `$inputs`, `$world` and "
      "`$pattern`. On a round clock each round is one interval, played after the round's stages; on a continuous "
      "clock intervals of `interval` follow each other from time 0. Results: $world.<name>_intervals (one record per "
      "interval: offered, answered, within, abandoned, callbacks, retrials, service_level, asa, abandon_rate, staff, "
      "utilisation, queue, max_queue, paid_hours, cost, and per channel and pool) and $world.<name>_totals; outputs "
      "<name>_service_level, _asa, _abandon_rate, _utilisation, _offered, _abandoned, _cost, _paid_hours, "
      "_intervals_below_target, and _offered_by_interval, _staff_by_interval, _service_level_by_interval, "
      "_abandon_rate_by_interval; metrics <name>_service_level, _offered, _staff and _waiting (the latest interval). "
      "Rates count customers who joined the line (offered less callbacks taken): service level is the share answered "
      "within the channel's `threshold`. Queue operations cost O(log n); arrivals and each customer's durations come "
      "from streams of their own, so arms with different staffing see the same customers.",
      example={"unit": "second", "interval": 1800,
               "channels": {"calls": {"arrivals": "$inputs.calls[$interval]",
                                      "service": {"dist": "lognormal", "mean": 380, "cv": 0.6},
                                      "patience": {"mean": 160}, "threshold": 20, "target": 0.8,
                                      "callback": {"when": 90, "accept": 0.6}}},
               "servers": {"agents": {"staff": "$inputs.staffing[$interval]", "cost": 26, "shrinkage": 0.3}}})
def _expand_queue(name: str, config: QueueConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    _check(config)
    clock = contract.get("clock") or {}
    _, delay = interval_length(config, clock if isinstance(clock, Mapping) else {})
    channels = list(config.channels)
    state = {**empty_state(), "now": {}}
    world = {
        f"{name}_state": {"type": "map", "default": state, "description": "The queue between intervals (internal)."},
        f"{name}_intervals": {"type": "list", "default": [], "description": "One record per interval played."},
        f"{name}_totals": {"type": "map", "default": empty_totals(channels),
                           "description": "Totals over every interval."},
    }
    events: list[dict[str, Any]]
    if delay is None:
        events = [{"name": f"{name}: interval", "phase": "end", "do": [{"economy": name, "action": "tick"}]}]
    else:
        events = [{"name": f"{name}: open", "at": 1, "do": [{"economy": name, "action": "open"}]}]
    totals, intervals = f"$world.{name}_totals", f"$world.{name}_intervals"
    outputs: dict[str, Any] = {
        f"{name}_service_level": {"expr": f"{totals}.service_level", "type": "number", "format": "pct",
                                  "description": "Share of customers who joined the line answered within the "
                                                 "threshold."},
        f"{name}_asa": {"expr": f"{totals}.asa", "type": "number", "format": "1",
                        "description": f"Average speed of answer ({config.unit}s)."},
        f"{name}_aht": {"expr": f"{totals}.aht", "type": "number", "format": "1",
                        "description": f"Average handle time of customers served ({config.unit}s)."},
        f"{name}_abandon_rate": {"expr": f"{totals}.abandon_rate", "type": "number", "format": "pct",
                                 "description": "Share of customers who joined the line and gave up."},
        f"{name}_utilisation": {"expr": f"{totals}.utilisation", "type": "number", "format": "pct",
                                "description": "Server time spent serving over server time on duty."},
        f"{name}_offered": {"expr": f"{totals}.offered", "type": "int", "description": "Customers who arrived."},
        f"{name}_abandoned": {"expr": f"{totals}.abandoned", "type": "int", "description": "Customers who gave up."},
        f"{name}_cost": {"expr": f"{totals}.cost", "type": "number", "format": "money",
                         "description": "Paid server hours × cost."},
        f"{name}_paid_hours": {"expr": f"{totals}.paid_hours", "type": "number", "format": "1",
                               "description": "Server hours paid (staff on duty grossed up for shrinkage)."},
        f"{name}_intervals_below_target": {"expr": f"{totals}.intervals_below_target", "type": "int",
                                           "description": "Intervals where a channel's service level was below its "
                                                          "target."},
        f"{name}_worst_interval_service_level": {
            "expr": f"$min($filter({intervals}, $it.service_level != null), $it.service_level) "
                    f"if $count({intervals}, $it.service_level != null) > 0 else null",
            "type": "number", "format": "pct",
            "description": "The lowest service level of any interval with customers (a per-interval guarantee to "
                           "constrain: >= 0.8 in 90% of runs)."},
        f"{name}_offered_by_interval": {"expr": f"$map({intervals}, $it.offered)", "type": "list"},
        f"{name}_staff_by_interval": {"expr": f"$map({intervals}, $it.staff)", "type": "list"},
        f"{name}_service_level_by_interval": {"expr": f"$map({intervals}, $it.service_level)", "type": "list"},
        f"{name}_abandon_rate_by_interval": {"expr": f"$map({intervals}, $it.abandon_rate)", "type": "list"},
    }
    if any(c.callback for c in config.channels.values()):
        outputs[f"{name}_callbacks"] = {"expr": f"{totals}.callbacks", "type": "int", "description": "Callbacks taken."}
        outputs[f"{name}_callbacks_unserved"] = {"expr": f"{totals}.callbacks_waiting", "type": "int",
                                                 "description": "Callbacks still waiting when the run ended."}
    if any(c.retry for c in config.channels.values()):
        outputs[f"{name}_retrials"] = {"expr": f"{totals}.retrials", "type": "int", "description": "Retries."}
    metrics = {
        f"{name}_service_level": {"expr": f"{totals}.latest.service_level",
                                  "description": "Service level of the latest interval."},
        f"{name}_offered": {"expr": f"{totals}.latest.offered",
                            "description": "Customers offered in the latest interval."},
        f"{name}_staff": {"expr": f"{totals}.latest.staff", "description": "Servers on duty in the latest interval."},
        f"{name}_waiting": {"expr": f"{totals}.waiting", "description": "Customers waiting now."},
    }
    return {"world": world, "events": events, "outputs": outputs, "metrics": metrics}


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------


def _config(world: Any, name: str, where: str) -> QueueConfig:
    raw = world.contract.mechanisms.get(name)
    if not isinstance(raw, Mapping) or f"{raw.get('kind')}.{raw.get('mode')}" != KEY:
        raise RunError(f"'{name}' is not a declared economy queue", where)
    return parsed(raw, QueueConfig)


def _number(world: Any, raw: Any, path: str, index: int, low: float | None = None, high: float | None = None,
            above: bool = False) -> float:
    value = raw
    if isinstance(raw, str):
        try:
            value = compile_expr(raw)(world.scope(interval=index))
        except ExprError as exc:
            raise RunError(str(exc), path) from None
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or value != value
        or value in (float("inf"), float("-inf"))):
        raise RunError(f"must give a finite number, got {value!r}", path)
    if (low is not None and (value < low or (above and value == low))) or (high is not None and value > high):
        bound = f"above {low:g}" if above else f"at least {low:g}" if high is None else f"from {low:g} to {high:g}"
        raise RunError(f"must be {bound}, got {value:g} (interval {index})", path)
    return float(value)


def _duration(world: Any, spec: DurationSpec, path: str, index: int) -> dict[str, Any]:
    low = _number(world, spec.low, f"{path}.low", index, 0.0)
    high = _number(world, spec.high, f"{path}.high", index, low)
    mean = ((low + high) / 2 if spec.dist == "uniform"
            else _number(world, spec.mean, f"{path}.mean", index, 0.0, above=True))
    cv = _number(world, spec.cv, f"{path}.cv", index, 0.0, above=True)
    return {"dist": spec.dist, "mean": mean, "cv": cv, "k": spec.k, "low": low, "high": high}


def resolve(world: Any, name: str, config: QueueConfig, index: int) -> dict[str, Any]:
    """Every number of interval ``index``, as plain data (kept in the state on a continuous clock)."""
    base = f"mechanisms.{name}"
    channels = {}
    for cname, spec in config.channels.items():
        path = f"{base}.channels.{cname}"
        callback = retry = None
        if spec.callback is not None:
            callback = [_number(world, spec.callback.when, f"{path}.callback.when", index, 0.0),
                        _number(world, spec.callback.accept, f"{path}.callback.accept", index, 0.0, 1.0),
                        _number(world, spec.callback.reserve, f"{path}.callback.reserve", index, 0.0)]
        if spec.retry is not None:
            retry = [_number(world, spec.retry.chance, f"{path}.retry.chance", index, 0.0, 1.0),
                     _duration(world, spec.retry.delay, f"{path}.retry.delay", index), spec.retry.max]
        channels[cname] = {"arrivals": _number(world, spec.arrivals, f"{path}.arrivals", index, 0.0),
                           "service": _duration(world, spec.service, f"{path}.service", index),
                           "patience": None if spec.patience is None
                           else _duration(world, spec.patience, f"{path}.patience", index),
                           "priority": spec.priority, "threshold": spec.threshold, "callback": callback, "retry": retry}
    pools = {}
    for pname, pool in config.servers.items():
        path = f"{base}.servers.{pname}"
        staff = _number(world, pool.staff, f"{path}.staff", index, 0.0)
        if staff != int(staff):
            raise RunError(f"must give a whole number of servers, got {staff:g} (interval {index}); round it, e.g. "
                           f"$round(...)", f"{path}.staff")
        pools[pname] = {"staff": int(staff),
                        "skills": list(config.channels) if pool.skills == "all" else list(pool.skills),
                        "cost": _number(world, pool.cost, f"{path}.cost", index, 0.0),
                        "shrinkage": _number(world, pool.shrinkage, f"{path}.shrinkage", index, 0.0, 0.99)}
    return {"channels": channels, "pools": pools}


def _engine_inputs(now: Mapping[str, Any]) -> tuple[dict[str, Channel], dict[str, Pool]]:
    channels = {}
    for cname, c in now["channels"].items():
        retry = c["retry"]
        channels[cname] = Channel(cname, c["arrivals"], Duration(**c["service"]),
                                  None if c["patience"] is None else Duration(**c["patience"]), c["priority"],
                                  c["threshold"], tuple(c["callback"]) if c["callback"] else None,  # type: ignore[arg-type]
                                  (retry[0], Duration(**retry[1]), retry[2]) if retry else None)
    pools = {pname: Pool(pname, p["staff"], tuple(p["skills"])) for pname, p in now["pools"].items()}
    return channels, pools


def _play(world: Any, name: str, config: QueueConfig, now: dict[str, Any]) -> dict[str, Any]:
    """Play the interval the state is at with ``now``'s numbers and write the results; the new state."""
    length, _ = interval_length(config, _clock_data(world))
    state = world.props[f"{name}_state"]
    index = int(state["interval"])
    channels, pools = _engine_inputs(now)
    engine_state, counts = run_interval(state, length, channels, pools, world.seeds, name)
    targets = {cname: spec.target for cname, spec in config.channels.items()}
    hours = length * UNIT_SECONDS[config.unit] / 3600.0
    record = record_for(index, index * length, length, hours, now, counts, targets)
    records, changed = merge_counts([*world.props[f"{name}_intervals"], record], counts, targets)
    totals = updated_totals(world.props[f"{name}_totals"], record, changed, length, engine_state)
    new_state = {**engine_state, "now": {}}
    world.set_world(f"{name}_intervals", records, trusted=True)
    world.set_world(f"{name}_totals", latest(totals, records[index]), trusted=True)
    return new_state


def _clock_data(world: Any) -> dict[str, Any]:
    clock = world.contract.clock
    return {"unit": clock.unit, "step": clock.step, "mode": clock.mode}


@family_action("economy", ("queue",), "tick", internal=True,
               example='{"economy": "centre", "action": "tick"}  (play the next interval)')
def _tick(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config = _config(world, name, where)
    state = world.props[f"{name}_state"]
    index = int(state["interval"])
    if not world.continuous:
        world.set_world(f"{name}_state", _play(world, name, config, resolve(world, name, config, index)), trusted=True)
        return
    new_state = _play(world, name, config, state["now"])
    _, delay = interval_length(config, _clock_data(world))
    assert delay is not None
    if world.horizon is None or world.time + delay <= world.horizon + 1e-9:
        new_state["now"] = resolve(world, name, config, index + 1)
        world.schedule(world.time + delay, [{"economy": name, "action": "tick"}], {}, f"mechanisms.{name}")
    world.set_world(f"{name}_state", new_state, trusted=True)


@family_action("economy", ("queue",), "open", internal=True,
               example='{"economy": "centre", "action": "open"}  (continuous clock: read the first interval and '
                       'schedule it)')
def _open(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config = _config(world, name, where)
    state = world.props[f"{name}_state"]
    _, delay = interval_length(config, _clock_data(world))
    assert delay is not None
    world.set_world(f"{name}_state", {**state, "now": resolve(world, name, config, int(state["interval"]))},
                    trusted=True)
    world.schedule(world.time + delay, [{"economy": name, "action": "tick"}], {}, f"mechanisms.{name}")
