"""Series outputs every round, typed outputs at the end, and the run result."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from ..contract import Contract
from ..contract.inputs import check_value
from ..errors import Issue, RunError
from ..expr import ExprError, compile_expr
from ..expr.template import apply_format
from ..world.live import SdkWorld, _plain

__all__ = ["RunResult", "sample_metrics", "compute_outputs", "ending"]


@dataclass
class RunResult:
    """Everything a run produced. ``outputs`` follows the contract's output contract."""

    status: str
    ended_by: str | None
    rounds: int
    seed: int
    arm: str | None
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    #: Each series output's latest sample, and every sample (``outputs.<x>.series``).
    metrics: dict[str, Any]
    series: dict[str, list[Any]]
    winner: Any = None
    error: str | None = None
    #: Each seat's return (its type's `score` value), in seat order; empty when no type scores.
    returns: dict[str, float] = field(default_factory=dict)
    output_issues: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    #: ``stats`` per agent entity id: its turns, calls, invalid calls, actions and model usage.
    agent_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    #: What each agent was shown on every wake, when recorded (see :mod:`fg_env.information.exposure`).
    exposures: dict[str, Any] = field(default_factory=dict)
    #: Spectator views rendered at the end of every round: ``[{round, views: {name: text}, final?}]``.
    frames: list[dict[str, Any]] = field(default_factory=list)
    #: The host answers of a run that recorded exposures (with ``exposures``, everything a replay needs).
    host_tape: dict[str, Any] = field(default_factory=dict)
    #: The run's budget: ``{limits, on_exhaust, used, exhausted}`` (empty without one).
    budget: dict[str, Any] = field(default_factory=dict)
    #: How :meth:`summary` shows the outputs that declare a `format`, by output name.
    formats: dict[str, str] = field(default_factory=dict)
    #: Likely logic problems the run revealed: ``[{code, path, message, fix}]`` (see :mod:`fg_env.runtime.diagnostics`).
    diagnostics: list[dict[str, str]] = field(default_factory=list)
    #: The clock the rounds count: ``{mode, unit, step, start}`` (empty for results saved before it was recorded).
    clock: dict[str, Any] = field(default_factory=dict)
    #: The assets the run knew — its catalog and submitted files — as metadata with content hashes (empty without any).
    assets: dict[str, Any] = field(default_factory=dict)
    #: The world as the run left it, kept small: world props, and per type its living count and first few entities
    #: (see :mod:`fg_env.runtime.end_state`). :meth:`summary` shows it.
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The run reached its end, its outputs are sound and it shows how the environment plays (nothing in
        :attr:`degraded`). A run its budget cut short is not ok: its outputs are those of an unfinished game."""
        return self.status in ("completed", "ended") and not self.output_issues and not self.degraded

    @property
    def degraded(self) -> list[str]:
        """The codes of the diagnostics that mean this run does not show what the environment is for — an action no
        agent could ever take, agents that never acted or whose turns mostly failed, agents that never had an action
        to take, turns lost to a failing provider, a budget that cut the run short, host answers that were the
        contract's stand-ins, an output that raised an error (``output_failed``; an output that is only null is not
        one). Empty for a sound run; a degraded run is not :attr:`ok`."""
        from .diagnostics import DEGRADING

        return list(dict.fromkeys(found["code"] for found in self.diagnostics if found["code"] in DEGRADING))

    def to_dict(self, events: bool = True) -> dict[str, Any]:
        out = asdict(self)
        if not events:
            out.pop("events")
        return out

    def to_json(self, events: bool = True, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(events), indent=indent, default=str, ensure_ascii=False)

    def save(self, path: Any) -> None:
        """Write the result to ``path``: JSON, or JSON lines when the name ends in ``.jsonl``."""
        from .result_file import save_result

        save_result(self, path)

    @classmethod
    def load(cls, path: Any) -> RunResult:
        """A result written by :meth:`save` (or printed by ``fg-env run --json``)."""
        from .result_file import load_result

        return load_result(path)

    @property
    def unit(self) -> str:
        """What one round is called: ``week``, ``half-hour`` (a clock of 30 minutes), ``round`` (see
        :mod:`fg_env.runtime.clock_words`)."""
        from .clock_words import unit_word

        return unit_word(self.clock)

    def period(self, round_: int, capital: bool = False) -> str:
        """A round named as a reader counts it: ``Week 7 (2026-10-12)`` on a dated weekly clock, ``09:30–10:00`` on a
        dated clock of half-hours, else ``round 7``."""
        from .clock_words import period_label

        return period_label(self.clock, round_, capital=capital, rounds=self.rounds)

    def summary(self) -> str:
        from .clock_words import plural

        how = ending(self.status, self.ended_by)
        lines = [f"{self.status} after {self.rounds} {plural(self.unit, self.rounds)}{how} (seed {self.seed}"
                 f"{', arm ' + self.arm if self.arm else ''})"]
        if self.error:
            lines.append(f"error: {self.error}")
        if self.degraded:
            lines.append(f"DEGRADED ({', '.join(self.degraded)}): this run does not show how the environment plays; "
                         "see the diagnostics below")
        if self.winner is not None and "winner" not in self.outputs:
            lines.append(f"winner: {self.winner}")
        for key, value in self.outputs.items():
            lines.append(f"{key}: {shown(value, self.formats.get(key))}")
        for found in self.diagnostics:
            lines.append(f"diagnostic: {found['path']}: {found['message']} → {found['fix']}")
        if self.budget.get("exhausted"):
            from .budget import spent

            key = self.budget["exhausted"]
            lines.append(f"budget: {key} ran out ({spent(key, self.budget['used'][key], self.budget['limits'][key])})")
        from .end_state import state_lines

        return "\n".join(lines + state_lines(self.state, self.series))


def shown(value: Any, fmt: str | None = None) -> str:
    """A value as a person reads it: with its declared template format, else JSON with numbers to 4 decimals
    (small numbers keep 3 significant digits). The stored value is never changed."""
    if fmt and value is not None and not isinstance(value, (list, dict)):
        return apply_format(value, fmt)
    text = json.dumps(_readable(value), default=str)
    return text if len(text) <= 120 else text[:117] + "…"


def ending(status: str, ended_by: str | None) -> str:
    """What stopped a run, for its summary line: what ended it, or that it was stopped before its end."""
    if ended_by:
        return f" — ended by {ended_by}"
    return " — stopped before the end" if status == "running" else ""


def _readable(value: Any) -> Any:
    if isinstance(value, float) and math.isfinite(value) and value:
        return round(value, max(READABLE_DECIMALS, 2 - math.floor(math.log10(abs(value)))))
    if isinstance(value, list):
        return [_readable(item) for item in value]
    if isinstance(value, dict):
        return {key: _readable(item) for key, item in value.items()}
    return value


#: Decimals :func:`shown` keeps when a value declares no format.
READABLE_DECIMALS = 4


def sample_metrics(contract: Contract, world: SdkWorld) -> None:
    """Sample every series output against the current world and append it to its series (``$outputs.x`` in the
    expressions reads the outputs sampled before it this round)."""
    scope = world.scope()
    values: dict[str, Any] = {}
    for name, spec in contract.series_outputs().items():
        try:
            value = _plain(compile_expr(spec.sampled)(scope.child(outputs=values)))
        except ExprError as exc:
            raise RunError(str(exc), f"outputs.{name}" + (".series" if isinstance(spec.series, str) else "")) from None
        values[name] = value
        world.series.setdefault(name, []).append(value)
        world.touch()
    world.metrics.clear()
    world.metrics.update(values)
    world.touch()


def compute_outputs(contract: Contract, world: SdkWorld) -> tuple[dict[str, Any], list[Issue]]:
    from .returns import run_result

    scope = world.scope(result=run_result(world))
    known = dict(world.metrics)  # what `$outputs` reads: series outputs' latest samples, and each output worked out
    outputs: dict[str, Any] = {}
    issues: list[Issue] = []
    for name, spec in contract.outputs.items():
        if spec.series is True:  # its result is its last sample
            value = world.metrics.get(name)
        else:
            try:
                value = _plain(compile_expr(spec.expr)(scope.child(outputs=known)))
            except ExprError as exc:
                issues.append(Issue(f"outputs.{name}", exc.detail, "fix the expression or guard missing values"))
                outputs[name] = known[name] = None
                continue
        if isinstance(value, float) and not math.isfinite(value):
            issues.append(Issue(f"outputs.{name}", "is not a finite number"))
            value = None
        problem = check_value(spec.type, value) if value is not None and spec.type != "any" else None
        if problem:
            issues.append(Issue(f"outputs.{name}", problem, f"declared type is {spec.type}"))
        outputs[name] = known[name] = value
    return outputs, issues


def _usable_output(result: RunResult, name: str) -> bool:
    """A healthy output can still be used when a different output failed."""
    return result.status != "failed" and not any(
        issue.get("path") == f"outputs.{name}" for issue in result.output_issues)
