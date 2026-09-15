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
    #: Turns a participant gave up after a provider error (``on_error="end_turn"``).
    forfeits: int = 0
    #: Turns that ran past their time limit.
    timeouts: int = 0
    #: Atomic turns undone because the whole turn was not `valid`.
    undone_turns: int = 0

    def add(self, other: "Stats") -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = asdict(self)
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
    events: List[Dict[str, Any]] = field(default_factory=list)
    #: What each agent was shown on every wake, when recorded (see :mod:`fg_env.sdk.exposure`).
    exposures: Dict[str, Any] = field(default_factory=dict)
    #: Spectator views rendered at the end of every round: ``[{round, views: {name: text}, final?}]``.
    frames: List[Dict[str, Any]] = field(default_factory=list)

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

    def summary(self) -> str:
        how = f"ended by {self.ended_by}" if self.ended_by else self.status
        lines = [f"{self.status} after {self.rounds} round(s) — {how} (seed {self.seed}{', arm ' + self.arm if self.arm else ''})"]
        if self.error:
            lines.append(f"error: {self.error}")
        if self.winner is not None and "winner" not in self.outputs:
            lines.append(f"winner: {self.winner}")
        for key, value in self.outputs.items():
            lines.append(f"{key}: {_short(value)}")
        for issue in self.output_issues:
            lines.append(f"output issue: {issue['path']}: {issue['message']}")
        return "\n".join(lines)


def _short(value: Any) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= 120 else text[:117] + "…"


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
    scope = world.scope()
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
