"""What a report is written from: the options a decision chooses between (arms, sweep cells, one run), their runs,
paired differences, a validation, and the contract when it is given — plus the decision rule that picks among them."""
from __future__ import annotations

import math
import operator
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..analysis.stats import quantile
from ..analysis.sweep import SweepResult
from ..analysis.validate import ValidationResult
from ..api import ContractLike, parse
from ..contract import Contract
from ..experiments.experiment import ExperimentResult
from ..runtime.measure import RunResult, _usable_output

__all__ = ["Option", "Summary", "Goal", "Requirement", "Evidence", "Choice", "gather", "choose", "summary",
           "parse_goal", "parse_requirements"]

#: The share of runs a reported range holds: from the 10th to the 90th percentile.
RANGE = 0.8


@dataclass
class Option:
    """One alternative a decision chooses between: an arm, a sweep cell, or the run(s) given."""

    label: str
    description: str
    runs: List[RunResult]
    inputs: Dict[str, Any] = field(default_factory=dict)
    failed: int = 0
    rounds: Optional[int] = None

    def values(self, measure: str) -> List[float]:
        return [float(v) for r in self.runs if _usable_output(r, measure) for v in [r.outputs.get(measure)]
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    median: float
    low: float
    high: float


def summary(values: Sequence[float]) -> Optional[Summary]:
    """Mean, median and the range holding the middle 80% of ``values``."""
    if not values:
        return None
    tail = (1.0 - RANGE) / 2.0
    return Summary(len(values), math.fsum(values) / len(values), quantile(values, 0.5), quantile(values, tail),
                   quantile(values, 1.0 - tail))


@dataclass(frozen=True)
class Goal:
    measure: str
    direction: str  # min | max

    def better(self, a: float, b: float) -> bool:
        return a < b if self.direction == "min" else a > b


_OPS: Dict[str, Callable[[float, float], bool]] = {">=": operator.ge, "<=": operator.le, ">": operator.gt,
                                                   "<": operator.lt}


@dataclass(frozen=True)
class Requirement:
    measure: str
    op: str
    value: float

    def met(self, value: float) -> bool:
        return _OPS[self.op](value, self.value)


def parse_goal(text: Optional[str]) -> Optional[Goal]:
    """``"min:cost"`` or ``"max:profit"``."""
    if text is None:
        return None
    direction, _, measure = str(text).partition(":")
    if direction not in ("min", "max") or not measure:
        raise ValueError(f"objective must be 'min:<output>' or 'max:<output>', got {text!r}")
    return Goal(measure, direction)


def parse_requirements(require: Optional[Mapping[str, Any]]) -> List[Requirement]:
    """``{"service_level": ">= 0.8"}``: every output that must meet a bound (on its mean over runs)."""
    out = []
    for measure, rule in (require or {}).items():
        text = str(rule).strip()
        op = next((o for o in (">=", "<=", ">", "<") if text.startswith(o)), None)
        try:
            value = float(text[len(op):]) if op else math.nan
        except ValueError:
            value = math.nan
        if op is None or not math.isfinite(value):
            raise ValueError(f"require[{measure!r}] must be like '>= 0.8' (>=, <=, > or < and a number), got {rule!r}")
        out.append(Requirement(measure, op, value))
    return out


@dataclass
class Evidence:
    kind: str  # run | runs | experiment | sweep | validation
    options: List[Option]
    control: Optional[str] = None
    deltas: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    sweep: Optional[SweepResult] = None
    validation: Optional[ValidationResult] = None
    contract: Optional[Contract] = None
    name: str = ""

    @property
    def runs(self) -> List[RunResult]:
        return [r for option in self.options for r in option.runs]

    @property
    def first(self) -> Optional[RunResult]:
        return self.runs[0] if self.runs else None

    def option(self, label: str) -> Optional[Option]:
        return next((o for o in self.options if o.label == label), None)


def _described(contract: Optional[Contract], arm: Optional[str]) -> str:
    if contract is not None and arm is not None and arm in contract.arms:
        return contract.arms[arm].description.rstrip(".")
    return ""


def _split(runs: Sequence[RunResult]) -> Tuple[List[RunResult], int]:
    kept = [r for r in runs if r.status != "failed"]
    return kept, len(runs) - len(kept)


def gather(source: Any, contract: Optional[ContractLike], validation: Optional[ValidationResult],
           control: Optional[str], data_dir: Any) -> Evidence:
    """Evidence from a run, a list of runs, an experiment, a sweep or a validation."""
    parsed = parse(contract, data_dir) if contract is not None else None
    if isinstance(source, ValidationResult):
        return Evidence("validation", [], validation=source, contract=parsed, name=source.contract)
    if isinstance(source, RunResult):
        kept, failed = _split([source])
        return Evidence("run", [Option(source.arm or "run", _described(parsed, source.arm), kept, dict(source.inputs),
                                       failed)], validation=validation, contract=parsed)
    if isinstance(source, ExperimentResult):
        options = []
        for label, arm in source.arms.items():
            kept, failed = _split(arm.runs)
            inputs = dict(parsed.arms[arm.arm].inputs) if parsed is not None and arm.arm in parsed.arms else {}
            options.append(Option(label, _described(parsed, arm.arm), kept, inputs, failed, source.rounds))
        chosen = control if control is not None else (options[0].label if options else None)
        if chosen is not None and chosen not in source.arms:
            raise ValueError(f"control {chosen!r} is not an arm of the experiment (arms: {', '.join(source.arms)})")
        deltas = source.deltas(chosen) if len(options) > 1 else {}
        return Evidence("experiment", options, chosen, deltas, validation=validation, contract=parsed)
    if isinstance(source, SweepResult):
        options = []
        for cell in source.cells:
            kept, failed = _split(cell.runs)
            options.append(Option(cell.label(), "", kept, dict(cell.inputs), failed, source.rounds))
        return Evidence("sweep", options, sweep=source, validation=validation, contract=parsed, name=source.contract)
    if isinstance(source, Sequence) and source and all(isinstance(r, RunResult) for r in source):
        kept, failed = _split(list(source))
        return Evidence("runs", [Option("runs", "", kept, dict(source[0].inputs), failed)], validation=validation,
                        contract=parsed)
    raise TypeError("report needs a RunResult, a list of RunResults, an ExperimentResult, a SweepResult or a "
                    f"ValidationResult, got {type(source).__name__}")


@dataclass
class Choice:
    """The option a decision rule picks, and how every option fared against it."""

    best: Optional[Option]
    goal: Optional[Goal]
    requirements: List[Requirement]
    #: ``{label: {requirement measure: share of runs meeting it}}``.
    meeting: Dict[str, Dict[str, float]] = field(default_factory=dict)
    feasible: List[str] = field(default_factory=list)
    excluded: Dict[str, str] = field(default_factory=dict)


def choose(options: Sequence[Option], goal: Optional[Goal], requirements: Sequence[Requirement]) -> Choice:
    """The option whose mean meets every requirement and is best on the goal (none without a goal or a feasible
    option)."""
    meeting: Dict[str, Dict[str, float]] = {}
    feasible: List[Option] = []
    excluded: Dict[str, str] = {}
    for option in options:
        reasons = []
        if option.failed:
            reasons.append(f"{option.failed} run(s) failed")
        unfinished = sum(
            r.status not in ("completed", "ended") and not
            (r.status == "running" and option.rounds is not None and r.rounds == option.rounds)
            for r in option.runs)
        exhausted = sum(bool(r.budget.get("exhausted")) for r in option.runs)
        if exhausted:
            reasons.append(f"{exhausted} run(s) exhausted their budget")
        if unfinished:
            reasons.append(f"{unfinished} run(s) are unfinished")
        measures = {r.measure for r in requirements} | ({goal.measure} if goal else set())
        missing = sorted(m for m in measures if len(option.values(m)) != len(option.runs))
        if missing:
            reasons.append("missing finite values for " + ", ".join(missing))
        if reasons:
            excluded[option.label] = "; ".join(reasons)
        shares, ok = {}, bool(option.runs) and not reasons
        for requirement in requirements:
            values = option.values(requirement.measure)
            shares[requirement.measure] = sum(requirement.met(v) for v in values) / len(values) if values else 0.0
            mean = summary(values)
            ok = ok and mean is not None and requirement.met(mean.mean)
        meeting[option.label] = shares
        if ok:
            feasible.append(option)
    best = None
    if goal is not None:
        for option in feasible:
            mine = summary(option.values(goal.measure))
            kept = summary(best.values(goal.measure)) if best is not None else None
            if mine is not None and (kept is None or goal.better(mine.mean, kept.mean)):
                best = option
    return Choice(best, goal, list(requirements), meeting, [o.label for o in feasible], excluded)
