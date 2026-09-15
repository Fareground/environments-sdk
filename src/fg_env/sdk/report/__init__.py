"""Reports a manager or an owner can act on: ``fg_env.report(result, audience="owner")``.

A report turns a run, an experiment, a sweep or a validation into short sentences and a few compact tables, in the
clock's own terms (half-hours, days, weeks): the recommended decision and its expected outcome with ranges, what
drives it (the patterns behind the busiest time, the options' paired differences, sweep effects, a typical run's
notable moments), the risks (options that miss the requirement, runs that did, the data check's warnings), what the
model assumes, and how well it matched the data. ``audience="analyst"`` adds the method: seeds, the decision rule,
every output of every option, and the full validation and sweep reports. ``Report.markdown`` and ``Report.to_dict()``
export it; ``fg-env report`` prints it.

A decision rule picks the option: ``objective="min:centre_cost"`` and ``require={"centre_service_level": ">= 0.8"}``
(requirements on the mean over runs). A contract with a service queue whose channels set a `target` gets that rule
by default: the cheapest staffing that meets the target on average.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from ..analysis.validate import ValidationResult
from ..api import ContractLike
from .evidence import Choice, Evidence, Goal, Requirement, choose, gather, parse_goal, parse_requirements
from .queue import QueueView, queues_in
from .sections import Section, assumptions, decision, drivers, fit, method, risks
from .words import Namer

__all__ = ["report", "Report", "AUDIENCES"]

AUDIENCES = ("owner", "analyst")
#: Outputs an owner reads when no decision rule names them: those with a display format, at most this many.
_KEY_MEASURES = 4


@dataclass
class Report:
    title: str
    audience: str
    kind: str
    sections: List[Section]
    recommendation: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)

    @property
    def markdown(self) -> str:
        parts = [f"# {self.title}"]
        for section in self.sections:
            parts.append(f"## {section.title}")
            if section.lines:
                parts.append("\n".join(f"- {line}" for line in section.lines))
            parts += [table.markdown() for table in section.tables]
        return "\n\n".join(parts) + "\n"

    def __str__(self) -> str:
        return self.markdown

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "audience": self.audience, "kind": self.kind, "recommendation": self.recommendation,
                "sections": [s.to_dict() for s in self.sections], "notes": self.notes}

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    def save(self, path: Union[str, Path]) -> None:
        """Write the report: JSON when the name ends in ``.json``, Markdown otherwise."""
        target = Path(path)
        target.write_text(self.to_json() if target.suffix == ".json" else self.markdown, encoding="utf-8")


def report(source: Any, audience: str = "owner", *, contract: Optional[ContractLike] = None,
           validation: Optional[ValidationResult] = None, objective: Optional[str] = None,
           require: Optional[Mapping[str, Any]] = None, control: Optional[str] = None, data_dir: Any = None) -> Report:
    """A plain-language report of ``source``: a :class:`~fg_env.RunResult` (or a list of them), an experiment, a sweep
    or a validation (see the module). ``contract`` adds names, arm descriptions, assumptions and pattern
    decompositions; ``validation`` adds how well the model matched the data; ``objective`` and ``require`` choose the
    recommended option; ``control`` is the arm differences are measured against (default: the first)."""
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {', '.join(AUDIENCES)}, got {audience!r}")
    ev = gather(source, contract, validation, control, data_dir)
    first = ev.first
    outputs = first.outputs if first is not None else {}
    queues = [QueueView.of(name, first, ev.contract) for name in queues_in(outputs)] if first is not None else []
    goal, requirements = _rule(parse_goal(objective), parse_requirements(require), queues, objective, require)
    namer = Namer(first.formats if first is not None else _formats(ev.contract),
                  [q.name for q in queues] or _declared_queues(ev.contract),
                  first.clock if first is not None else _clock(ev.contract), ev.contract, {q.name: q.unit for q in queues})
    measures = _measures(outputs, goal, requirements, queues, first.formats if first is not None else {})
    choice = choose(ev.options, goal, requirements) if goal is not None else Choice(None, None, requirements)
    owner = audience == "owner"
    sections = []
    if ev.options:
        sections += [decision(ev, choice, namer, measures, queues), drivers(ev, choice, namer, measures, queues, owner),
                     risks(ev, choice, namer, measures, queues, owner)]
    sections += [assumptions(ev, queues), fit(ev, namer)]
    if not owner:
        sections.append(method(ev, namer, choice))
    title = (ev.contract.name if ev.contract is not None else ev.name) or "Model report"
    recommendation = None
    if choice.best is not None:
        recommendation = {"option": choice.best.label, "description": choice.best.description,
                          "inputs": choice.best.inputs,
                          "objective": (objective or f"{goal.direction}:{goal.measure}") if goal else None,
                          "require": {r.measure: f"{r.op} {r.value:g}" for r in requirements}}
    return Report(title, audience, ev.kind, sections, recommendation)


def _declared_queues(contract: Any) -> List[str]:
    """Service queues a contract declares (a report written without runs, such as a validation's, names by them)."""
    if contract is None:
        return []
    return [name for name, raw in contract.mechanisms.items()
            if isinstance(raw, Mapping) and (raw.get("kind"), raw.get("mode")) == ("operations", "queue")]


def _formats(contract: Any) -> Dict[str, str]:
    return {name: spec.format for name, spec in contract.outputs.items() if spec.format} if contract is not None else {}


def _clock(contract: Any) -> Dict[str, Any]:
    if contract is None:
        return {}
    clock = contract.clock
    return {"mode": clock.mode, "unit": clock.unit, "step": clock.step}


def _rule(goal: Optional[Goal], requirements: List[Requirement], queues: List[QueueView], objective: Any,
          require: Any) -> tuple:
    """The decision rule given, or a service queue's default: the cheapest plan meeting its service target."""
    if objective is not None or require is not None or not queues:
        return goal, requirements
    view = next((q for q in queues if q.target is not None), None)
    if view is None:
        return goal, requirements
    return Goal(f"{view.name}_cost", "min"), [Requirement(f"{view.name}_service_level", ">=", float(view.target or 0))]


def _measures(outputs: Mapping[str, Any], goal: Optional[Goal], requirements: List[Requirement],
              queues: List[QueueView], formats: Mapping[str, str]) -> List[str]:
    chosen: List[str] = []
    for name in [*(r.measure for r in requirements), *([goal.measure] if goal else [])]:
        if name not in chosen:
            chosen.append(name)
    for view in queues:
        for part in ("service_level", "abandon_rate", "asa", "cost"):
            name = f"{view.name}_{part}"
            if name in outputs and name not in chosen:
                chosen.append(name)
    if not chosen:
        numeric = [k for k, v in outputs.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        chosen = ([k for k in numeric if k in formats] or numeric)[:_KEY_MEASURES]
    return chosen
