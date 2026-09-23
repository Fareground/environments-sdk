"""Metrics every round, typed outputs at the end, and the run result."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .contract import Contract
from .errors import Issue, RunError
from .expr import ExprError, compile_expr
from .inputs import check_value
from .template import apply_format
from .world import SdkWorld, _plain

__all__ = ["Stats", "RunResult", "sample_metrics", "compute_outputs"]


@dataclass
class Stats:
    """How the run went for its agents — the numbers that keep an environment LLM-native."""

    wakes: int = 0
    calls: int = 0
    actions: int = 0
    invalid_calls: int = 0
    rejected_actions: int = 0
    idle_turns: int = 0
    brief_chars: int = 0
    update_chars: int = 0
    tools_offered: int = 0
    #: Turns whose brief / update was actually read (coded participants often read neither).
    brief_reads: int = 0
    update_reads: int = 0
    #: Reported by LLM participants (see :meth:`Wake.record_usage`): real provider numbers, not estimates.
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_retries: int = 0
    #: Turns played without waking the agent (stage `auto`).
    auto_turns: int = 0
    #: Out-of-turn reaction turns (`wake` with `now`).
    reactions: int = 0
    #: Turns an LLM participant lost because its provider still failed after every retry.
    forfeits: int = 0
    #: Model replies cut off at their output limit (reported by LLM participants).
    truncated: int = 0
    #: Model replies the provider refused to give (reported by LLM participants).
    refusals: int = 0
    #: Turns that ran past their time limit.
    timeouts: int = 0
    #: Atomic turns undone because the whole turn was not `valid`.
    undone_turns: int = 0
    #: Actions refused and undone because a rule failed or an invariant broke while they applied (also counted in
    #: ``rejected_actions``): a contract bug, explained in the run's diagnostics.
    faulted_actions: int = 0

    def add(self, other: "Stats") -> None:
        for name, value in vars(other).items():  # every field is a count: most of a turn's are zero
            if value:
                setattr(self, name, getattr(self, name) + value)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = dict(vars(self))
        wakes = max(1, self.wakes)
        out["avg_update_tokens"] = round(self.update_chars / max(1, self.update_reads) / 4)
        out["avg_brief_tokens"] = round(self.brief_chars / max(1, self.brief_reads) / 4)
        out["avg_tools"] = round(self.tools_offered / wakes, 1)
        out["invalid_rate"] = round(self.invalid_calls / max(1, self.calls), 3)
        return out


@dataclass
class RunResult:
    """Everything a run produced. ``outputs`` follows the contract's output contract."""

    status: str
    ended_by: Optional[str]
    rounds: int
    seed: int
    arm: Optional[str]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    metrics: Dict[str, Any]
    series: Dict[str, List[Any]]
    winner: Any = None
    error: Optional[str] = None
    #: The clock time reached (continuous clock), else None.
    time: Optional[float] = None
    #: Each seat's return (the contract's `game.returns`), in seat order; empty when none is declared.
    returns: Dict[str, float] = field(default_factory=dict)
    output_issues: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    #: ``stats`` per agent entity id: its turns, calls, invalid calls, actions and model usage.
    agent_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    #: What each agent was shown on every wake, when recorded (see :mod:`fg_env.exposure`).
    exposures: Dict[str, Any] = field(default_factory=dict)
    #: Spectator views rendered at the end of every round: ``[{round, views: {name: text}, final?}]``.
    frames: List[Dict[str, Any]] = field(default_factory=list)
    #: The host answers of a run that recorded exposures (with ``exposures``, everything a replay needs).
    host_tape: Dict[str, Any] = field(default_factory=dict)
    #: The run's budget: ``{limits, on_exhaust, used, exhausted}`` (empty without one).
    budget: Dict[str, Any] = field(default_factory=dict)
    #: How :meth:`summary` shows the outputs that declare a `format`, by output name.
    formats: Dict[str, str] = field(default_factory=dict)
    #: Likely logic problems the run revealed: ``[{code, path, message, fix}]`` (see :mod:`fg_env.diagnostics`).
    diagnostics: List[Dict[str, str]] = field(default_factory=list)
    #: The clock the rounds count: ``{mode, unit, step, start}`` (empty for results saved before it was recorded).
    clock: Dict[str, Any] = field(default_factory=dict)
    #: The assets the run knew — its catalog and submitted files — as metadata with content hashes (empty without any).
    assets: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("completed", "ended") and not self.output_issues

    def to_dict(self, events: bool = True) -> Dict[str, Any]:
        out = asdict(self)
        if not events:
            out.pop("events")
        return out

    def to_json(self, events: bool = True, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(events), indent=indent, default=str, ensure_ascii=False)

    def save(self, path: Any) -> None:
        """Write the result to ``path``: JSON, or JSON lines when the name ends in ``.jsonl``."""
        from .result_file import save_result

        save_result(self, path)

    @classmethod
    def load(cls, path: Any) -> "RunResult":
        """A result written by :meth:`save` (or printed by ``fg-env run --json``)."""
        from .result_file import load_result

        return load_result(path)

    @property
    def unit(self) -> str:
        """What one round is called: ``week``, ``half-hour`` (a clock of 30 minutes), or ``round`` for a continuous
        clock (see :mod:`fg_env.clock_words`)."""
        from .clock_words import unit_word

        return unit_word(self.clock)

    def period(self, round_: int, capital: bool = False) -> str:
        """A round named as a reader counts it: ``Week 7 (2026-10-12)`` on a dated weekly clock, ``09:30–10:00`` on a
        dated clock of half-hours, else ``round 7``."""
        from .clock_words import period_label

        return period_label(self.clock, round_, capital=capital, rounds=self.rounds)

    def summary(self) -> str:
        from .clock_words import plural

        how = f"ended by {self.ended_by}" if self.ended_by else self.status
        lines = [f"{self.status} after {self.rounds} {plural(self.unit, self.rounds)} — {how} (seed {self.seed}"
                 f"{', arm ' + self.arm if self.arm else ''})"]
        if self.error:
            lines.append(f"error: {self.error}")
        if self.winner is not None and "winner" not in self.outputs:
            lines.append(f"winner: {self.winner}")
        for key, value in self.outputs.items():
            lines.append(f"{key}: {shown(value, self.formats.get(key))}")
        for issue in self.output_issues:
            lines.append(f"output issue: {issue['path']}: {issue['message']}")
        for found in self.diagnostics:
            lines.append(f"diagnostic: {found['path']}: {found['message']} → {found['fix']}")
        if self.budget.get("exhausted"):
            from .budget import spent

            key = self.budget["exhausted"]
            lines.append(f"budget: {key} ran out ({spent(key, self.budget['used'][key], self.budget['limits'][key])})")
        return "\n".join(lines)


def shown(value: Any, fmt: Optional[str] = None) -> str:
    """A value as a person reads it: with its declared template format, else JSON with numbers to 4 decimals
    (small numbers keep 3 significant digits). The stored value is never changed."""
    if fmt and value is not None and not isinstance(value, (list, dict)):
        return apply_format(value, fmt)
    text = json.dumps(_readable(value), default=str)
    return text if len(text) <= 120 else text[:117] + "…"


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
    """Evaluate every metric against the current world and append it to its series."""
    scope = world.scope()
    values: Dict[str, Any] = {}
    for name, spec in contract.metrics.items():
        try:
            value = _plain(compile_expr(spec.expr)(scope.child(metrics=values)))
        except ExprError as exc:
            raise RunError(str(exc), f"metrics.{name}") from None
        values[name] = value
        world.series.setdefault(name, []).append(value)
        world.touch()
    world.metrics.clear()
    world.metrics.update(values)
    world.touch()


def compute_outputs(contract: Contract, world: SdkWorld) -> tuple[Dict[str, Any], List[Issue]]:
    from .returns import run_result

    scope = world.scope(result=run_result(world))
    outputs: Dict[str, Any] = {}
    issues: List[Issue] = []
    for name, spec in contract.outputs.items():
        try:
            value = _plain(compile_expr(spec.expr)(scope.child(outputs=outputs)))
        except ExprError as exc:
            issues.append(Issue(f"outputs.{name}", exc.detail, "fix the expression or guard missing values"))
            outputs[name] = None
            continue
        if isinstance(value, float) and not math.isfinite(value):
            issues.append(Issue(f"outputs.{name}", "is not a finite number"))
            value = None
        problem = check_value(spec.type, value) if value is not None and spec.type != "any" else None
        if problem:
            issues.append(Issue(f"outputs.{name}", problem, f"declared type is {spec.type}"))
        outputs[name] = value
    return outputs, issues


def _usable_output(result: RunResult, name: str) -> bool:
    """A healthy output can still be used when a different output failed."""
    return result.status != "failed" and not any(
        issue.get("path") == f"outputs.{name}" for issue in result.output_issues)
