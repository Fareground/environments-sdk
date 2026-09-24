"""The service-queue part of a report: the staffing plan in the clock's terms, service by time of day, what makes
the busiest time busy, and what the queue model assumes.

A queue is recognised by the outputs its mechanism generates (``<name>_staff_by_interval`` and
``<name>_service_level_by_interval``), so a saved run is enough; the contract, when given, adds the pool's name
(agents, doctors), the time unit, service targets and the patterns behind arrivals.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..analysis.decompose import decompose
from ..runtime.clock_words import plural, span_label, unit_word
from ..runtime.measure import RunResult, _usable_output
from .evidence import Option, summary
from .words import Namer

__all__ = ["QueueView", "queues_in", "blocks"]

#: A plan with more changes than this is told by its range and peak; its table lists every change.
_PLAN_CHANGES_TOLD = 6
_PATTERN = re.compile(r"\$pattern\.([A-Za-z_][A-Za-z0-9_]*)")
_INPUT = re.compile(r"\$inputs\.([A-Za-z_][A-Za-z0-9_]*)")


def queues_in(outputs: Mapping[str, Any]) -> list[str]:
    """Names of the service queues whose outputs a run has."""
    return sorted(name[: -len("_staff_by_interval")] for name in outputs
                  if name.endswith("_staff_by_interval")
                  and f"{name[: -len('_staff_by_interval')]}_service_level_by_interval" in outputs)


def blocks(staff: Sequence[Any]) -> list[tuple[int, int, int]]:
    """``(first interval, last interval, staff)`` for every stretch of equal staffing."""
    out: list[tuple[int, int, int]] = []
    for index, value in enumerate(staff):
        count = int(value)
        if out and out[-1][2] == count:
            out[-1] = (out[-1][0], index, count)
        else:
            out.append((index, index, count))
    return out


@dataclass
class QueueView:
    name: str
    config: dict[str, Any]
    clock: dict[str, Any]
    rounds: int
    #: The input values the queue's numbers read (a run's inputs, or the contract's defaults).
    inputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, name: str, run: RunResult, contract: Any) -> QueueView:
        raw = contract.mechanisms.get(name) if contract is not None else None
        return cls(name, dict(raw) if isinstance(raw, Mapping) else {}, dict(run.clock or {}), run.rounds,
                   dict(run.inputs))

    @property
    def unit(self) -> str:
        return str(self.config.get("unit") or "second")

    @property
    def server_word(self) -> str:
        servers = self.config.get("servers") or {}
        return next(iter(servers)).replace("_", " ") if len(servers) == 1 else "staff"

    @property
    def target(self) -> float | None:
        targets = [c.get("target") for c in (self.config.get("channels") or {}).values() if isinstance(c, Mapping)]
        found = [float(t) for t in targets if isinstance(t, (int, float))]
        return min(found) if found else None

    def when(self, first: int, last: int) -> str:
        return span_label(self.clock, first + 1, last + 1, rounds=self.rounds)

    def staff(self, option: Option) -> list[int]:
        key = f"{self.name}_staff_by_interval"
        plan = next((run.outputs[key] for run in option.runs
                     if _usable_output(run, key) and isinstance(run.outputs.get(key), list)), None)
        return [int(v) for v in plan] if isinstance(plan, list) else []

    @classmethod
    def declared(cls, name: str, contract: Any, inputs: Mapping[str, Any]) -> QueueView:
        """A queue read from its contract alone (an optimisation's report has no runs): the clock's start and length
        come from ``inputs`` or the inputs' defaults."""
        clock = contract.clock
        values = {key: spec.default for key, spec in contract.inputs.items()}
        values.update(inputs)

        def resolved(raw: Any) -> Any:
            text = str(raw) if raw is not None else ""
            return values.get(text[len("$inputs."):]) if text.startswith("$inputs.") else raw

        rounds = resolved(clock.rounds)
        return cls(name, dict(contract.mechanisms.get(name) or {}),
                   {"unit": clock.unit, "step": clock.step, "start": resolved(clock.start)},
                   rounds if isinstance(rounds, int) else 0, values)

    def staffing_input(self) -> str | None:
        """The list input a single pool's staff reads per interval (``$inputs.<name>[$interval]``), if any."""
        servers = [spec for spec in (self.config.get("servers") or {}).values() if isinstance(spec, Mapping)]
        found = re.search(r"\$inputs\.([A-Za-z_][A-Za-z0-9_]*)\[\$interval\]", str(servers[0].get("staff"))) \
            if len(servers) == 1 else None
        return found.group(1) if found else None

    def plan_sentence(self, option: Option) -> str:
        return self.plan_text(self.staff(option))

    def plan_text(self, staff: Sequence[int]) -> str:
        spans = blocks(staff)
        if not spans:
            return ""
        word = self.server_word
        if len(spans) <= _PLAN_CHANGES_TOLD:
            parts = [f"{count} {word if count != 1 else word.rstrip('s')} {self.when(a, b)}" for a, b, count in spans]
            return "Staff " + ", ".join(parts) + "."
        peak = max(spans, key=lambda s: (s[2], -s[0]))
        low = min(count for _, _, count in spans)
        return (f"Staff between {low} and {peak[2]} {word} per {unit_word(self.clock)}, most ({peak[2]}) "
                f"{self.when(peak[0], peak[1])}; the plan table lists every change.")

    def _per_interval(self, option: Option, output: str) -> list[list[float]]:
        """For each interval, the value of ``output`` in every run (runs without one left out)."""
        columns: list[list[float]] = []
        key = f"{self.name}_{output}"
        for run in option.runs:
            if not _usable_output(run, key):
                continue
            values = run.outputs.get(key)
            for index, value in enumerate(values if isinstance(values, list) else []):
                while len(columns) <= index:
                    columns.append([])
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    columns[index].append(float(value))
        return columns

    def plan_rows(self, option: Option, namer: Namer) -> list[list[str]]:
        """One row per stretch of equal staffing: when, staff, customers, service level (80% range), worst interval."""
        offered = self._per_interval(option, "offered_by_interval")
        service = self._per_interval(option, "service_level_by_interval")
        rows = []
        for a, b, count in blocks(self.staff(option)):
            per_run: list[float] = []
            worst: list[float] = []
            for run in range(len(option.runs)):
                weights = [(offered[i][run], service[i][run]) for i in range(a, b + 1)
                           if i < len(offered) and i < len(service) and run < len(offered[i]) and run < len(service[i])]
                total = sum(w for w, _ in weights)
                if total > 0:
                    per_run.append(sum(w * s for w, s in weights) / total)
                    worst.append(min(s for w, s in weights if w > 0))
            customers = summary([sum(offered[i][run] for i in range(a, b + 1) if i < len(offered) and run
                                     < len(offered[i]))
                                 for run in range(len(option.runs))])
            level, low = summary(per_run), summary(worst)
            rows.append([self.when(a, b), str(count), f"{customers.median:,.0f}" if customers else "—",
                         _ranged(level) if level else "—", f"{low.median:.0%}" if low else "—"])
        return rows

    def busiest(self, option: Option) -> int | None:
        offered = self._per_interval(option, "offered_by_interval")
        means = [summary(column) for column in offered]
        ranked = [(s.mean, -i) for i, s in enumerate(means) if s is not None]
        return -max(ranked)[1] if ranked else None

    def peak_driver(self, option: Option, contract: Any) -> str | None:
        """What makes the busiest interval busy: every factor of the product patterns its arrivals read."""
        index = self.busiest(option)
        if contract is None or index is None:
            return None
        channels = self.config.get("channels") or {}
        names = [n for c in channels.values() if isinstance(c, Mapping)
                 for n in _PATTERN.findall(str(c.get("arrivals", "")))]
        for pattern in dict.fromkeys(names):
            spec = contract.patterns.get(pattern) or {}
            if spec.get("kind") != "product" or spec.get("keys") or spec.get("table"):
                continue
            row = decompose(contract, pattern, rounds=[index + 1], inputs=option.inputs).rows[0]
            adds = sorted(row["adds"].items(), key=lambda item: -abs(item[1]))
            told = [f"{_factor_name(contract, factor, row)} {'adds' if amount >= 0 else 'takes away'} "
                    f"{abs(amount):,.0f}"
                    for factor, amount in adds if abs(amount) >= 0.5]
            customers = summary(self._per_interval(option, "offered_by_interval")[index])
            if not told or customers is None:
                continue
            return (f"The busiest {unit_word(self.clock)} is {self.when(index, index)}, with about "
                    f"{customers.mean:,.0f} customers: of the {row['total']:,.0f} expected, " + ", ".join(told) + ".")
        return None

    def horizon_driver(self, option: Option, contract: Any) -> str | None:
        """What each factor of the product patterns its arrivals read adds over the whole run: the day of the week, and
        time of day at its busiest and quietest (see :mod:`.pattern_effects`)."""
        run = option.runs[0] if option.runs else None
        if contract is None or run is None:
            return None
        from .pattern_effects import Effects, clauses, factor_of

        for channel, spec in (self.config.get("channels") or {}).items():
            for pattern in dict.fromkeys(_PATTERN.findall(str(spec.get("arrivals", "")) if isinstance(spec, Mapping)
                                                          else "")):
                raw = contract.patterns.get(pattern) or {}
                if raw.get("kind") != "product" or raw.get("keys") or raw.get("table"):
                    continue
                rows = decompose(contract, pattern, rounds=list(range(1, self.rounds + 1)), inputs=run.inputs,
                                 seed=run.seed, estimates=True).rows
                factors = {name: factor_of(contract, name) for name in rows[0]["factors"]} if rows else {}
                effects = Effects()
                for index, row in enumerate(rows):
                    slots = {name: self._slot(name, contract, row, index) for name, f in factors.items() if f.how
                             == "calendar"}
                    effects.add(index + 1, float(row["total"]), {n: float(v) for n, v in row["factors"].items()}, slots)
                told = clauses(effects, factors, unit_word(self.clock),
                               f"{self.rounds} {plural(unit_word(self.clock), 2)}")
                if told:
                    return (f"Over the day, of about {effects.amount:,.0f} expected {channel.replace('_', ' ')}: "
                            + "; ".join(told.values()) + ".")
        return None

    def _slot(self, pattern: str, contract: Any, row: Mapping[str, Any], index: int) -> tuple[str, str]:
        if (contract.patterns.get(pattern) or {}).get("period") == "week" and row.get("date"):
            import datetime as _dt

            day = _dt.date.fromisoformat(str(row["date"])[:10]).strftime("%A")
            return day, f"on {day}s"
        label = self.when(index, index)
        return label, f"at {label}"

    def assumptions(self, owner: bool = False) -> list[str]:
        """How the queue behaves, in the reader's words; the analyst also reads each duration's distribution."""
        return [re.sub(r" \((?:exponential|lognormal|gamma|normal|uniform|erlang)\)", "", text) if owner else text
                for text in self._assumed()]

    def _assumed(self) -> list[str]:
        out = []
        for name, channel in (self.config.get("channels") or {}).items():
            if not isinstance(channel, Mapping):
                continue
            service = channel.get("service") or {}
            patience = channel.get("patience")
            text = (f"{name.replace('_', ' ').capitalize()} arrive at random at the expected rate of each "
                    f"{unit_word(self.clock)}; service takes {_duration(service, self.unit, self.inputs)}")
            text += (f"; customers give up after waiting {_duration(patience, self.unit, self.inputs)}"
                     if isinstance(patience, Mapping)
                     else "; nobody gives up waiting")
            if isinstance(channel.get("callback"), Mapping):
                callback = channel["callback"]
                when, adjusted = _amount(callback.get("when", 0), self.inputs)
                text += (f"; a callback is offered when the wait would pass {when} {plural(self.unit, 2)}"
                         + (" (normally)" if adjusted else ""))
            out.append(text + ".")
        return out


def _ranged(level: Any) -> str:
    return f"{level.median:.0%} ({level.low:.0%}–{level.high:.0%})"


def _amount(raw: Any, inputs: Mapping[str, Any]) -> tuple[str, bool]:
    """A config number as a reader sees it, and whether an expression adjusts it: a literal, the input it reads, or
    (for an expression that scales an input, like patience during an outage) that input's value, normally."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return f"{raw:g}", False
    names = _INPUT.findall(str(raw))
    value = inputs.get(names[0]) if names else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "a value the contract computes", False
    return f"{value:g}", _INPUT.fullmatch(str(raw).strip()) is None


def _duration(spec: Mapping[str, Any], unit: str, inputs: Mapping[str, Any]) -> str:
    dist = str(spec.get("dist") or "exponential")
    amount, adjusted = _amount(spec.get("mean"), inputs)
    shown = f"{amount} {plural(unit, 2)}" + ("" if dist == "fixed" else f" on average ({dist})")
    return shown + (", changed by the contract at times" if adjusted else "")


def _factor_name(contract: Any, factor: str, row: Mapping[str, Any]) -> str:
    spec = contract.patterns.get(factor) or {}
    name = str(spec.get("description") or factor.replace("_", " ")).split(" (")[0].rstrip(".")
    if spec.get("kind") == "seasonal" and spec.get("period") == "week" and row.get("date"):
        import datetime as _dt

        name += f" ({_dt.date.fromisoformat(str(row['date'])[:10]).strftime('%A')})"
    return name
