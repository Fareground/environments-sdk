"""Reports a manager or an owner can act on: ``fg_env.report(result, audience="owner")``.

A report turns a run, an experiment, a sweep, a validation or an optimisation into short sentences and a few compact
tables, in the clock's own terms (half-hours, days, weeks): the recommended decision and its expected outcome with
ranges, what drives it (the patterns behind the busiest time, the options' paired differences, sweep effects, a
typical run's notable moments, what one step either way from an optimised decision breaks), the risks (options that
miss the requirement, runs that did, a decision that won by seed luck, the data check's warnings), what the model
assumes, and how well it matched the data. ``audience="analyst"`` adds the method: seeds, the decision rule, every
output of every option, and the full validation, sweep and optimisation reports. ``Report.markdown`` and
``Report.to_dict()`` export it; ``fg-env report`` prints it.

A decision rule picks among an experiment's or a sweep's options: ``objective="min:centre_cost"`` and
``require={"centre_service_level": ">= 0.8"}`` (requirements on the mean over runs). A contract with a service queue
whose channels set a `target` gets that rule by default: the cheapest staffing that meets the target on average. An
optimisation (``fg_env.optimise``) brings its own rule; pass it as the source, or as ``optimisation=`` next to an
experiment that plays its decision, and the report says how sure the optimiser is.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from ..analysis.optimise_result import OptimisationResult
from ..analysis.validate import ValidationResult
from ..api import ContractLike, parse
from . import optimisation as optimised
from .confidence import assess
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
           validation: Optional[ValidationResult] = None, optimisation: Optional[OptimisationResult] = None,
           objective: Optional[str] = None, require: Optional[Mapping[str, Any]] = None, control: Optional[str] = None,
           data_dir: Any = None) -> Report:
    """A plain-language report of ``source``: a :class:`~fg_env.RunResult` (or a list of them), an experiment, a sweep,
    a validation or an optimisation (see the module). ``contract`` adds names, arm descriptions, assumptions and
    pattern decompositions; ``validation`` adds how well the model matched the data; ``optimisation`` adds how the
    decision an experiment plays was found; ``objective`` and ``require`` choose the recommended option; ``control``
    is the arm differences are measured against (default: the first)."""
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {', '.join(AUDIENCES)}, got {audience!r}")
    if isinstance(source, OptimisationResult):
        optimisation, source = source, None
    if source is not None:
        ev = gather(source, contract, validation, control, data_dir)
    elif validation is not None:
        ev = gather(validation, contract, None, None, data_dir)
    elif optimisation is not None:
        ev = Evidence("optimisation", [], contract=parse(contract, data_dir) if contract is not None else None,
                      name=optimisation.contract)
    else:
        raise TypeError("report needs a result to report on, a validation or an optimisation")
    if optimisation is not None and not ev.options:
        ev.kind = "optimisation"
    owner = audience == "owner"
    first = ev.first
    outputs = first.outputs if first is not None else {}
    if first is not None:
        queues = [QueueView.of(name, first, ev.contract) for name in queues_in(outputs)]
    else:
        queues = [QueueView.declared(name, ev.contract, (optimisation.best or {}) if optimisation else {})
                  for name in _declared_queues(ev.contract)]
    measures_known = list(outputs) or (list(ev.contract.outputs) if ev.contract is not None else [])
    goal, requirements = _rule(parse_goal(objective), parse_requirements(require), queues, objective, require,
                               optimisation)
    namer = Namer(first.formats if first is not None else _formats(ev.contract), [q.name for q in queues],
                  first.clock if first is not None else (queues[0].clock if queues else _clock(ev.contract)),
                  ev.contract, {q.name: q.unit for q in queues})
    measures = _measures(outputs, goal, requirements, queues, first.formats if first is not None else {})
    choice = choose(ev.options, goal, requirements) if goal is not None else Choice(None, None, requirements)
    sure = assess(ev, choice, measures)
    sections: List[Section] = []
    if ev.options:
        sections += [decision(ev, choice, namer, measures, queues, sure),
                     drivers(ev, choice, namer, measures, queues, owner), risks(ev, choice, namer, measures, queues, owner)]
    if optimisation is not None:
        _add_optimisation(sections, optimisation, namer, queues, measures_known, owner)
    sections += [assumptions(ev, queues, owner), fit(ev, namer)]
    if not owner:
        sections.append(method(ev, namer, choice))
        if optimisation is not None:
            sections[-1].lines += optimised.summary(optimisation)
    title = (ev.contract.name if ev.contract is not None else ev.name or (optimisation.contract if optimisation else "")) \
        or "Model report"
    recommendation = optimised.as_dict(optimisation) if optimisation is not None else None
    if choice.best is not None:
        recommendation = {**(recommendation or {}), "option": choice.best.label, "description": choice.best.description,
                          "inputs": choice.best.inputs,
                          "objective": (objective or f"{goal.direction}:{goal.measure}") if goal else None,
                          "require": {r.measure: f"{r.op} {r.value:g}" for r in requirements},
                          "confidence": sure.to_dict()}
    return Report(title, audience, ev.kind, sections, recommendation)


def _section(sections: List[Section], title: str, position: int) -> Section:
    found = next((s for s in sections if s.title == title), None)
    if found is None:
        found = Section(title)
        sections.insert(min(position, len(sections)), found)
    return found


def _add_optimisation(sections: List[Section], opt: OptimisationResult, namer: Namer, queues: List[QueueView],
                      measures: List[str], owner: bool) -> None:
    """The optimiser's recommendation, drivers and risks, merged into the sections an experiment already wrote."""
    lines = optimised.recommendation(opt, namer, queues, measures)
    if sections and sections[0].title in ("Recommendation", "What the model says"):
        head = sections[0]
        head.title = "Recommendation"
        head.lines = [line for line in head.lines if not line.startswith("No decision rule was given")]
    else:
        head = _section(sections, "Recommendation", 0)
    planned = any(line.startswith("Staff ") for line in head.lines)
    head.lines[:0] = [line for line in lines if not (planned and line.startswith("Staff "))]
    table = optimised.plan_table(opt, queues)
    if table is not None and not any(t.title == table.title for t in head.tables):
        head.tables.insert(0, table)
    _section(sections, "What drives it", 1).lines += optimised.drivers(opt, namer, measures)
    risk = _section(sections, "Risks", 2)
    risk.lines[:0] = optimised.risks(opt, namer, measures)
    if len(risk.lines) > 1 and "No risk stands out in these runs." in risk.lines:
        risk.lines.remove("No risk stands out in these runs.")
    if not risk.lines:
        risk.lines.append("No risk stands out: the choice held on fresh seeds.")
    driving = _section(sections, "What drives it", 1)
    if not driving.lines:
        driving.lines.append("One step either way from the chosen decision keeps every constraint.")


def _declared_queues(contract: Any) -> List[str]:
    """Service queues a contract declares (a report written without runs names by them)."""
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
          require: Any, optimisation: Optional[OptimisationResult]) -> tuple:
    """The decision rule given, or a service queue's default: the cheapest plan meeting its service target (none when
    an optimisation already chose)."""
    if objective is not None or require is not None or not queues or optimisation is not None:
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
        for part in ("service_level", "worst_interval_service_level", "abandon_rate", "asa", "cost"):
            name = f"{view.name}_{part}"
            if name in outputs and name not in chosen:
                chosen.append(name)
    if not chosen:
        numeric = [k for k, v in outputs.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        chosen = ([k for k in numeric if k in formats] or numeric)[:_KEY_MEASURES]
    return chosen
