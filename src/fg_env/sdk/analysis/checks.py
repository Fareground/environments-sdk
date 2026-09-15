"""Behavior checks: play a contract with random agents and report what looks broken.

A static check (``fg_env.check``) proves a contract is well formed; these checks look at what
actually happens when it runs. Each finding names the part of the contract, says in plain
language what was seen and what it usually means, and carries the evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..measure import RunResult
from . import runner

__all__ = ["behavior_checks", "CheckReport", "Finding"]

#: Share of random agents' actions refused by the rules above which tool schemas are suspect.
_REJECTION_SHARE = 0.5
#: Input types that can be varied automatically.
_SCALAR_TYPES = ("number", "int", "bool", "enum")


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # error | warning | info
    subject: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "subject": self.subject, "message": self.message,
                "evidence": self.evidence}


@dataclass
class CheckReport:
    contract: str
    runs: int
    rounds: Optional[int]
    findings: List[Finding]
    tested_inputs: List[str]
    untested_inputs: List[str]

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def codes(self) -> List[Tuple[str, str]]:
        return [(f.code, f.subject) for f in self.findings]

    def report(self) -> str:
        scope = f"{self.runs} run(s)" + (f" of {self.rounds} round(s)" if self.rounds else "")
        if not self.findings:
            return f"Behavior checks of {self.contract}: nothing suspicious in {scope} with random agents."
        lines = [f"Behavior checks of {self.contract}: {len(self.findings)} finding(s) in {scope} with random agents"]
        for f in self.findings:
            lines.append(f"{f.severity}: {f.subject}: {f.message}")
        if self.untested_inputs:
            lines.append(f"not varied (fixed by the caller, no default, or not a number, yes/no or choice): "
                         f"{', '.join(self.untested_inputs)}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "runs": self.runs, "rounds": self.rounds, "ok": self.ok,
                "findings": [f.to_dict() for f in self.findings], "tested_inputs": self.tested_inputs,
                "untested_inputs": self.untested_inputs}


def _variants(contract: Any, name: str, base: Any, perturb: float) -> List[Any]:
    spec = contract.inputs[name]
    if spec.type == "bool":
        return [not base] if isinstance(base, bool) else [True, False]
    if spec.type == "enum":
        return [v for v in (spec.values or []) if v != base][:2]
    if isinstance(base, bool) or not isinstance(base, (int, float)):
        return []
    if base != 0:
        raw = [base * (1 - perturb), base * (1 + perturb)]
    else:
        raw = [spec.min if spec.min is not None else -1.0, spec.max if spec.max is not None else 1.0]
    out = []
    for value in raw:
        value = max(value, spec.min) if spec.min is not None else value
        value = min(value, spec.max) if spec.max is not None else value
        coerced = int(round(value)) if spec.type == "int" else float(value)
        if spec.type == "int" and coerced == base:
            step = 1 if value >= base else -1
            coerced = base + step
            if (spec.min is not None and coerced < spec.min) or (spec.max is not None and coerced > spec.max):
                continue
        if coerced != base and coerced not in out:
            out.append(coerced)
    return out


def _fingerprint(result: RunResult) -> str:
    return json.dumps({"outputs": result.outputs, "series": result.series, "rounds": result.rounds,
                       "status": result.status}, sort_keys=True, default=str)


def behavior_checks(contract: ContractLike, *, runs: int = 4, rounds: Optional[int] = None, seed: int = 0,
                    participants: Any = "random", inputs: Optional[Mapping[str, Any]] = None,
                    test_inputs: Optional[Sequence[str]] = None, perturb: float = 0.5,
                    workers: int = 1, data_dir: Any = None, hosts: Any = None) -> CheckReport:
    """Run ``runs`` seeds with random agents (plus one set per varied input) and report findings.

    ``test_inputs`` limits which inputs are varied (default: every number, whole number, yes/no
    and choice input); each is moved by ±``perturb`` of its value (flipped, or set to another
    choice) on the same seeds, so any difference comes from the input. ``rounds`` caps each run:
    shorter runs are faster but can miss behaviour that only appears later, which the findings say.
    ``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers
    host requests in every run.
    """
    runner.check_positive_int("runs", runs)
    if not 0 < perturb < 1:
        raise ValueError(f"perturb must be between 0 and 1, got {perturb}")
    parsed = runner.as_contract(contract, data_dir)
    base_inputs = dict(inputs or {})
    seeds = runner.run_seeds(seed, runs)
    baseline_jobs = [runner.Job(base_inputs, None, s) for s in seeds]
    findings: List[Finding] = []
    try:
        baseline = runner.run_jobs(parsed, baseline_jobs, participants=participants, rounds=rounds, workers=workers,
                                   events=True, hosts=hosts)
    except runner.AnalysisError:  # every run failed: report the first error and stop
        probe = runner.execute_job(parsed, baseline_jobs[0], participants, rounds, False, hosts=hosts)
        findings.append(Finding("runs_fail", "error", "(run)", f"Every run failed: {probe.error}. "
                                "Fix this first; nothing else can be checked.", {"error": probe.error}))
        return CheckReport(parsed.name, runs, rounds, findings, [], list(parsed.inputs))
    completed = [r for r in baseline if r.status != "failed"]
    findings += _failures(baseline)
    findings += _output_findings(parsed, completed, rounds)
    findings += _metric_findings(parsed, completed)
    findings += _action_findings(parsed, completed, rounds)
    findings += _stage_findings(parsed, completed, rounds)
    tested, untested, input_findings = _input_findings(parsed, base_inputs, baseline, seeds, test_inputs, perturb,
                                                       participants, rounds, workers, hosts)
    findings += input_findings
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f.severity])
    return CheckReport(parsed.name, runs, rounds, findings, tested, untested)


def _failures(results: Sequence[RunResult]) -> List[Finding]:
    failed = [r for r in results if r.status == "failed"]
    if not failed:
        return []
    return [Finding("runs_fail", "error", "(run)", f"{len(failed)} of {len(results)} run(s) failed. First error: "
                    f"{failed[0].error}", {"seeds": [r.seed for r in failed], "error": failed[0].error})]


def _rounds_note(rounds: Optional[int]) -> str:
    return f" (runs were capped at {rounds} round(s); it may only happen later)" if rounds else ""


def _output_findings(contract: Any, runs: Sequence[RunResult], rounds: Optional[int]) -> List[Finding]:
    out: List[Finding] = []
    issues: Dict[str, List[str]] = {}
    for r in runs:
        for issue in r.output_issues:
            issues.setdefault(issue["path"], []).append(issue["message"])
    for path, messages in issues.items():
        out.append(Finding("output_issue", "warning", path, f"In {len(messages)} run(s) this output could not be "
                           f"computed or had the wrong type: {messages[0]}.", {"messages": messages[:5]}))
    if len(runs) < 2:
        return out
    for name in contract.outputs:
        values = [json.dumps(r.outputs.get(name), sort_keys=True, default=str) for r in runs]
        if f"outputs.{name}" in issues or len(set(values)) > 1:
            continue
        shown = values[0] if len(values[0]) <= 60 else values[0][:57] + "…"
        if runs[0].outputs.get(name) is None:
            out.append(Finding("output_always_empty", "warning", f"outputs.{name}",
                               f"The output was empty in all {len(runs)} run(s){_rounds_note(rounds)}."))
        else:
            out.append(Finding("output_never_varies", "warning", f"outputs.{name}",
                               f"The output was {shown} in all {len(runs)} run(s) despite different seeds. Expected "
                               "if the world has no randomness; otherwise it may not depend on anything that "
                               "happens.", {"value": runs[0].outputs.get(name)}))
    return out


def _metric_findings(contract: Any, runs: Sequence[RunResult]) -> List[Finding]:
    out = []
    for name in contract.metrics:
        seen = {json.dumps(v, sort_keys=True, default=str) for r in runs for v in r.series.get(name, [])}
        if len(seen) == 1:
            value = next(iter(seen))
            out.append(Finding("metric_constant", "warning", f"metrics.{name}",
                               f"The metric stayed at {value} in every round of every run. It may read something "
                               "that nothing changes.", {"value": json.loads(value)}))
    return out


def _action_findings(contract: Any, runs: Sequence[RunResult], rounds: Optional[int]) -> List[Finding]:
    taken: Dict[str, List[bool]] = {}
    for r in runs:
        for event in r.events:
            if event.get("kind") == "action":
                data = event.get("data") or {}
                taken.setdefault(str(data.get("action")), []).append(bool(data.get("success", True)))
    out = []
    for name, spec in contract.actions.items():
        by = spec.by if isinstance(spec.by, str) else ", ".join(spec.by)
        outcomes = taken.get(name)
        if not outcomes:
            out.append(Finding("action_never_taken", "warning", f"actions.{name}",
                               f"No agent took this action in {len(runs)} run(s) of random play{_rounds_note(rounds)}. "
                               f"Its conditions ('when') may never hold, no stage may offer it, or no '{by}' "
                               "agent may exist.", {"by": by}))
        elif not any(outcomes):
            out.append(Finding("action_never_succeeds", "warning", f"actions.{name}",
                               f"The action was tried {len(outcomes)} time(s) but never succeeded: its effects "
                               "may always fail or roll back.", {"attempts": len(outcomes)}))
    stats_actions = sum(r.stats.get("actions", 0) for r in runs)
    rejected = sum(r.stats.get("rejected_actions", 0) for r in runs)
    attempts = stats_actions + rejected
    if attempts and rejected / attempts > _REJECTION_SHARE:
        out.append(Finding("high_rejection", "info", "(actions)",
                           f"{rejected} of {attempts} action attempts by random agents were refused by the rules. "
                           "Tool parameters may allow choices that are rarely legal; tighter params ('where', "
                           "'min', 'max') help agents choose well.", {"rejected": rejected, "attempts": attempts}))
    return out


def _stage_has_actions(stage: Any) -> bool:
    actions = stage.actions
    if isinstance(actions, str):
        return True
    if isinstance(actions, Mapping):
        return any(bool(v) for v in actions.values())
    return bool(actions)


def _stage_findings(contract: Any, runs: Sequence[RunResult], rounds: Optional[int]) -> List[Finding]:
    acted = {e.get("stage") for r in runs for e in r.events if e.get("kind") == "action"}
    out = []
    for stage in contract.stages:
        if _stage_has_actions(stage) and stage.name not in acted:
            out.append(Finding("stage_never_acted", "warning", f"stages.{stage.name}",
                               f"Nobody acted in this stage in {len(runs)} run(s){_rounds_note(rounds)}: its 'when' "
                               "or 'who' may never be true, or none of its actions is ever available.",
                               {"when": stage.when, "who": stage.who}))
    return out


def _input_findings(contract: Any, base_inputs: Mapping[str, Any], baseline: Sequence[RunResult], seeds: Sequence[int],
                    test_inputs: Optional[Sequence[str]], perturb: float, participants: Any, rounds: Optional[int],
                    workers: int, hosts: Any) -> Tuple[List[str], List[str], List[Finding]]:
    names = list(test_inputs) if test_inputs is not None else list(contract.inputs)
    for name in names:
        runner.input_spec(contract, name)
    tested, untested = [], []
    plan: List[Tuple[str, Any]] = []
    for name in names:
        spec = contract.inputs[name]
        if name in base_inputs or spec.type not in _SCALAR_TYPES:
            untested.append(name)
            continue
        variants = _variants(contract, name, spec.default, perturb)
        if not variants:
            untested.append(name)
            continue
        tested.append(name)
        plan += [(name, v) for v in variants]
    if not plan:
        return tested, untested, []
    cells = [({**base_inputs, name: v}, None) for name, v in plan]
    jobs = runner.jobs_for(cells, seeds)
    try:
        results = runner.run_jobs(contract, jobs, participants=participants, rounds=rounds, workers=workers, hosts=hosts)
    except runner.AnalysisError:
        results = [runner.failed_result(job, RuntimeError("every variant run failed")) for job in jobs]
    grouped = runner.by_cell(jobs, results, len(cells))
    base_prints = [_fingerprint(r) if r.status != "failed" else None for r in baseline]
    changed: Dict[str, List[Any]] = {}
    broke: Dict[str, List[Tuple[Any, str]]] = {}
    for (name, v), cell in zip(plan, grouped):
        for base_print, result in zip(base_prints, cell):
            if result.status == "failed":
                broke.setdefault(name, []).append((v, result.error or "failed"))
            elif base_print is not None and _fingerprint(result) != base_print:
                changed.setdefault(name, []).append(v)
    findings = []
    for name in tested:
        tried = [v for n, v in plan if n == name]
        if name in broke:
            value, error = broke[name][0]
            findings.append(Finding("input_breaks_runs", "warning", f"inputs.{name}",
                                    f"Setting it to {value!r} made runs fail: {error}.", {"value": value, "error": error}))
        elif name not in changed:
            findings.append(Finding("input_has_no_effect", "warning", f"inputs.{name}",
                                    f"Changing it (tried {', '.join(repr(v) for v in tried)}) changed no output and "
                                    f"no metric in {len(seeds)} seeded run(s){_rounds_note(rounds)}. It may be unused, "
                                    "or only matter in situations these runs never reached.", {"tried": tried}))
    return tested, untested, findings
