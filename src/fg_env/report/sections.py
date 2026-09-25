"""The sections of a report, each a list of short sentences and a few compact tables."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..analysis.accuracy import bias_verdict
from ..analysis.highlights import highlights
from ..runtime.clock_words import plural, unit_word
from ..runtime.measure import usable_output
from .confidence import Confidence, interval, label
from .confidence import lines as confidence_lines
from .demand import demand_lines
from .evidence import Choice, Evidence, Option, summary
from .noise import MEANINGFUL_SHARE
from .queue import QueueView
from .words import Namer

__all__ = ["Table", "Section", "decision", "outcomes", "drivers", "risks", "assumptions", "fit", "method"]

#: Highlights at least this surprising count as a cause worth naming (a two-sided 10% tail).
_NOTABLE_SCORE = 1.645
#: How many causes and risks an owner reads; an analyst reads them all.
_OWNER_ITEMS = 5
#: Notable moments of a typical run an owner reads.
_TYPICAL_MOMENTS = 2
#: How a requirement that was not met reads: service level was "below" 80%.
_MISSED = {">=": "below", ">": "at or below", "<=": "above", "<": "at or above"}


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list[str]]

    def markdown(self) -> str:
        head = "| " + " | ".join(self.columns) + " |"
        rule = "|" + "|".join("---" for _ in self.columns) + "|"
        return "\n".join([f"**{self.title}**", "", head, rule, *("| " + " | ".join(row) + " |" for row in self.rows)])

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "columns": self.columns, "rows": self.rows}


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "lines": self.lines, "tables": [t.to_dict() for t in self.tables]}


def _ranged(namer: Namer, measure: str, option: Option) -> str | None:
    found = summary(option.values(measure))
    if found is None:
        return None
    if found.n == 1 or math.isclose(found.low, found.high):
        return namer.value(measure, found.median)
    return (f"{namer.value(measure, found.median)} (80% range "
            f"{namer.value(measure, found.low)}–{namer.value(measure, found.high)})")


def decision(ev: Evidence, choice: Choice, namer: Namer, measures: Sequence[str], queues: Sequence[QueueView],
             sure: Confidence) -> Section:
    confident = choice.best is not None and not sure.tied
    section = Section("Recommendation" if confident else "What the model says")
    subject = choice.best or (ev.options[0] if len(ev.options) == 1 else None)
    if choice.goal is not None and choice.best is None and ev.options:
        wanted = "; ".join(f"{namer.name(r.measure)} {r.op} {namer.value(r.measure, r.value)}"
                           for r in choice.requirements)
        if choice.excluded:
            section.lines.append("No option has complete valid evidence meeting the decision rule.")
        else:
            section.lines.append(f"No option meets {wanted or 'the goal'}; the table shows how close each came.")
    for option in ev.options:
        if option.label in choice.excluded:
            section.lines.append(f"{label(option, start=True)} is excluded from recommendations: "
                                 f"{choice.excluded[option.label]}. Its available outcomes are descriptive only.")
    if subject is not None:
        if choice.best is not None and len(ev.options) > 1:
            section.lines += confidence_lines(ev, choice, sure, namer)
        for view in queues:
            plan = view.plan_sentence(subject)
            if plan:
                section.lines.append(plan)
        expected = [f"{namer.name(m)} {text}" for m in measures for text in [_ranged(namer, m, subject)] if text]
        if expected:
            section.lines.append("Expected: " + "; ".join(expected) + ".")
        if ev.kind == "run":
            section.lines.append("This is one run of the model, so its numbers have no range: run an experiment for "
                                 "one.")
    if choice.goal is None and len(ev.options) > 1:
        section.lines.append("No decision rule was given (objective and require), so the options are compared, not "
                             "ranked.")
    if len(ev.options) > 1:
        section.tables.append(outcomes(ev, choice, namer, measures, sure))
    for view in queues:
        if subject is not None and view.staff(subject):
            section.tables.append(Table("Staffing plan", ["When", view.server_word.capitalize(), "Customers",
                                                          "Service level (80% range)",
                                                          f"Worst {unit_word(view.clock)}"],
                                        view.plan_rows(subject, namer)))
    return section


def outcomes(ev: Evidence, choice: Choice, namer: Namer, measures: Sequence[str], sure: Confidence) -> Table:
    """Every option's outcomes: the pick marked ✓, or the two options within noise of each other marked ≈."""
    tied = [choice.best, sure.runner_up] if sure.tied else []
    rows = []
    for option in ev.options:
        mark = " ≈" if any(option is o for o in tied) else " ✓" if choice.best is option else ""
        cells = [label(option, start=True) + mark]
        cells += [_ranged(namer, m, option) or "—" for m in measures]
        rows.append(cells)
    return Table("Options", ["Option", *[namer.name(m).capitalize() for m in measures]], rows)


def drivers(ev: Evidence, choice: Choice, namer: Namer, measures: Sequence[str], queues: Sequence[QueueView],
            owner: bool) -> Section:
    section = Section("What drives it")
    subject = choice.best or (ev.options[0] if ev.options else None)
    run = _representative(subject, choice, measures)
    for view in queues:
        if subject is not None:
            section.lines += [text for text in (view.peak_driver(subject, ev.contract),
                                                view.horizon_driver(subject, ev.contract)) if text]
    section.lines += demand_lines(ev.contract, run, _OWNER_ITEMS if owner else None)
    section.lines += _differences(ev, choice, namer, measures, owner)
    if ev.sweep is not None:
        section.lines += _sweep_drivers(ev, namer, measures, owner)
    if run is not None and (run.series or run.events):
        decided = {f"{view.name}_staff" for view in queues}  # the plan itself, not something that happened
        moments = [_named(h.text, run.series, namer) for h in highlights(run, top=_OWNER_ITEMS)
                   if h.score >= _NOTABLE_SCORE and h.subject not in decided]
        section.lines += [f"In a typical run, {text}." for text in moments[: _TYPICAL_MOMENTS if owner else None]]
    if not section.lines:
        section.lines.append("Nothing in these runs separates one outcome from another beyond chance.")
    return section


def _named(text: str, measures: Mapping[str, Any], namer: Namer) -> str:
    """A moment's text with every measure called by its reader's name (longest names first, so none is cut), and a
    share's changes in points and levels in percent (``service level fell 37 points``, not ``fell 0.3673``)."""
    for measure in sorted(measures, key=len, reverse=True):
        if measure not in text:
            continue
        name = namer.name(measure)
        text = text.replace(measure, name)
        if namer.is_share(measure):
            text = re.sub(rf"({re.escape(name)} (?:rose|fell)) (\d+(?:\.\d+)?(?:e-?\d+)?) \([^)]*\)",
                          lambda m: f"{m.group(1)} {float(m.group(2)) * 100:.0f} points", text)
            text = re.sub(rf"({re.escape(name)} (?:peaked|bottomed out) at) (\d+(?:\.\d+)?)",
                          lambda m: f"{m.group(1)} {float(m.group(2)):.0%}", text)
        elif namer.is_money(measure):
            text = re.sub(rf"({re.escape(name)} (?:rose|fell|peaked at|bottomed out at)) (\d+(?:\.\d+)?)",
                          lambda m: f"{m.group(1)} {namer.value(measure, float(m.group(2)))}", text)  # noqa: B023 — called within this iteration
    return re.sub(r"\bin day (\d+)", r"on day \1", text)


def _differences(ev: Evidence, choice: Choice, namer: Namer, measures: Sequence[str], owner: bool) -> list[str]:
    """Each option's clear paired difference from the control. An owner reads the outcomes the decision is judged on
    (its requirements, else its goal), strongest first; a difference that is the same in every run is the option
    itself (a staffing cost), not a cause, and is left to the analyst."""
    outcome = [r.measure for r in choice.requirements] or ([choice.goal.measure] if choice.goal else list(measures[:2]))
    control = ev.option(ev.control) if ev.control else None
    ranked = []
    for arm, per_output in ev.deltas.items():
        option = ev.option(arm)
        for measure in (outcome if owner else measures):
            found = per_output.get(measure)
            if option is None or not found or not found.get("clear"):
                continue
            low, high = found["ci95"]
            fixed = math.isclose(low, high)
            base = summary(control.values(measure)) if control is not None else None
            trivial = base is not None and abs(found["mean"]) < MEANINGFUL_SHARE * abs(base.mean)
            if owner and (fixed or trivial):
                continue
            interval = "the same in every run" if fixed else \
                f"95% CI {namer.change(measure, low)} to {namer.change(measure, high)}"
            strength = math.inf if fixed else abs(found["mean"]) / found["sd"] * math.sqrt(found["n"])
            ranked.append((strength, f"{label(option, start=True)}: {namer.name(measure)} "
                                     f"{namer.change(measure, found['mean'])} against "
                                     f"{label(control) if control else ev.control} ({interval})."))
    ranked.sort(key=lambda item: -item[0])
    return [text for _, text in ranked[: _OWNER_ITEMS if owner else None]]


def _sweep_drivers(ev: Evidence, namer: Namer, measures: Sequence[str], owner: bool) -> list[str]:
    assert ev.sweep is not None
    out = []
    for measure, effects in ev.sweep.main_effects().items():
        if measures and measure not in measures:
            continue
        spans = []
        for factor, effect in effects.items():
            levels = [row for row in effect.get("levels", []) if row.get("mean") is not None]
            if len(levels) < 2:
                continue
            spread = max(r["mean"] for r in levels) - min(r["mean"] for r in levels)
            moves = (f"{namer.name(measure)} from {namer.value(measure, levels[0]['mean'])} at {levels[0]['value']} to "
                     f"{namer.value(measure, levels[-1]['mean'])} at {levels[-1]['value']}")
            paired = effect.get("high_minus_low") or {}
            low, high = paired.get("low"), paired.get("high")
            scale = max(abs(r["mean"]) for r in levels)
            if low is not None and high is not None and (low <= 0 <= high or spread < MEANINGFUL_SHARE * scale):
                spans.append((0.0, f"{factor.replace('_', ' ')} makes no clear difference to {moves} "
                                   f"(within noise: {interval(namer, measure, low, high)})."))
            else:
                spans.append((spread, f"{factor.replace('_', ' ')} moves {moves}."))
        spans.sort(key=lambda item: -item[0])
        out += [text.capitalize() for _, text in spans[: 1 if owner else None]]
    return out


def _representative(option: Option | None, choice: Choice, measures: Sequence[str]) -> Any:
    if option is None or not option.runs:
        return None
    measure = choice.goal.measure if choice.goal else (measures[0] if measures else None)
    if measure is None:
        return option.runs[0]
    scored = [(r.outputs.get(measure), i) for i, r in enumerate(option.runs)
              if usable_output(r, measure) and isinstance(r.outputs.get(measure), (int, float))]
    if not scored:
        return option.runs[0]
    scored.sort()
    return option.runs[scored[len(scored) // 2][1]]


def risks(ev: Evidence, choice: Choice, namer: Namer, measures: Sequence[str], queues: Sequence[QueueView],
          owner: bool) -> Section:
    section = Section("Risks")
    for option in ev.options:
        invalid = [run for run in option.runs if run.output_issues]
        if invalid:
            first = invalid[0].output_issues[0]
            section.lines.append(f"{label(option, start=True)} had output issues in {len(invalid)} run(s): "
                                 f"{first['path']}: {first['message']}. Invalid values were excluded.")
    best = choice.best or (ev.options[0] if len(ev.options) == 1 else None)
    if best is not None:
        for requirement in choice.requirements:
            share = choice.meeting.get(best.label, {}).get(requirement.measure)
            if share is not None and share < 1:
                available = len(best.values(requirement.measure))
                missed = round((1 - share) * available)
                section.lines.append(f"{namer.name(requirement.measure).capitalize()} was {_MISSED[requirement.op]} "
                                     f"{namer.value(requirement.measure, requirement.value)} in {missed} of "
                                     f"{available} runs.")
        for view in queues:
            below = summary(best.values(f"{view.name}_intervals_below_target"))
            if below is not None and below.median > 0:
                count = int(below.median)
                section.lines.append(f"{count} {plural(unit_word(view.clock), count)} fall below the service target in "
                                     "a typical run.")
            unserved = summary(best.values(f"{view.name}_callbacks_unserved"))
            if unserved is not None and unserved.median > 0:
                section.lines.append(f"About {unserved.median:.0f} callbacks are still waiting at the end of a typical "
                                     "run.")
    for option in ev.options:
        if option is best or option.label in choice.feasible:
            continue
        failing = [r for r in choice.requirements
                   if (found := summary(option.values(r.measure))) is not None and not r.met(found.mean)]
        if failing:
            told = "; ".join(f"{namer.name(r.measure)} {_ranged(namer, r.measure, option)}" for r in failing)
            section.lines.append(f"With {label(option)}: {told}.")
    if ev.kind == "run":
        section.lines.append("One run shows one possible outcome; the range of outcomes is not known from it.")
    if ev.validation is not None:
        section.lines += _data_risks(ev, namer, owner)
    failed = sum(option.failed for option in ev.options)
    if failed:
        section.lines.append(f"{failed} run(s) failed and are left out.")
    if owner:
        section.lines = section.lines[:_OWNER_ITEMS]
    if not section.lines:
        section.lines.append("No risk stands out in these runs.")
    return section


def _data_risks(ev: Evidence, namer: Namer, owner: bool) -> list[str]:
    """What the data check found that a plan should allow for: ranges too narrow, forecasts that run high or low. The
    analyst reads the check's own warnings."""
    assert ev.validation is not None
    if not owner:
        return [f"The data check warns: {text}." for text in ev.validation.warnings]
    out = []
    for measure, found in ev.validation.measures.items():
        accuracy = found.get("held_out") or found["overall"]
        for level, row in (accuracy.get("coverage") or {}).items():
            if math.isclose(float(level), 0.8) and row["coverage_ci95"][1] < row["nominal"]:
                out.append(f"Ranges for {namer.name(measure)} are too narrow: its 80% ranges held "
                           f"{row['coverage']:.0%} of actual values, so plan with a margin.")
        if bias_verdict(accuracy) and accuracy.get("bias") is not None:
            out.append(f"{namer.name(measure).capitalize()} forecasts run {abs(accuracy['bias']):.0%} "
                       f"{'high' if accuracy['bias'] > 0 else 'low'}.")
    return out


def assumptions(ev: Evidence, queues: Sequence[QueueView], owner: bool) -> Section:
    """What the model takes as given: the queue's behaviour, inputs described as assumed, and how many parameters the
    data estimated. An owner reads values in words; the analyst also reads the inputs' names."""
    section = Section("What the model assumes")
    contract = ev.contract
    if contract is None:
        section.lines.append("The contract was not given, so its assumptions are not listed (pass contract=).")
        return section
    for view in queues:
        section.lines += view.assumptions(owner)
    assumed = [(name, spec) for name, spec in contract.inputs.items() if "assum" in spec.description.lower()]
    for name, spec in assumed:
        value = spec.default
        shown = f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        section.lines.append(f"{spec.description.rstrip('.')}, set to {shown}." if owner else
                             f"{spec.description.rstrip('.')} — {name.replace('_', ' ')} = {shown}.")
    fitted = [name for name, spec in contract.inputs.items()
              if "fitted by fg_env.analysis.fit_patterns" in spec.description]
    if fitted:
        count = (f"{len(fitted)} {plural('parameter', len(fitted))} {'is' if len(fitted) == 1 else 'are'} estimated "
                 "from the data")
        section.lines.append(f"{count}; the analyst report lists them." if owner else f"{count}: {', '.join(fitted)}.")
    if contract.description and not section.lines:
        section.lines.append(contract.description)
    return section


def fit(ev: Evidence, namer: Namer) -> Section:
    section = Section("How well it matched the data")
    validation = ev.validation
    if validation is None:
        section.lines.append("Not checked against data here: pass validation=fg_env.analysis.validate(contract, "
                             "cases).")
        return section
    for measure, found in validation.measures.items():
        held = found.get("held_out")
        accuracy = held or found["overall"]
        where = "held-out" if held else "historical"
        wape, bias = accuracy.get("wape"), accuracy.get("bias")
        text = (f"{namer.name(measure).capitalize()}: off by {wape:.0%} on average over {accuracy['n']} {where} "
                "value(s)") \
            if wape is not None else f"{namer.name(measure).capitalize()}: {accuracy['n']} {where} value(s)"
        if bias is not None:
            text += f"; forecasts ran {abs(bias):.0%} {'high' if bias > 0 else 'low'}" if bias_verdict(accuracy) \
                else "; no clear bias" if abs(bias) < 0.005 else (f"; no clear bias ({abs(bias):.0%} "
                                                                  f"{'high' if bias > 0 else 'low'}, within noise)")
        coverage = _coverage(accuracy)
        if coverage is not None:
            text += f"; its 80% ranges held {coverage:.0%} of actual values"
        section.lines.append(text + ".")
    section.lines.append(f"Checked on {len(validation.cases)} case(s) × {validation.runs} run(s).")
    return section


def _coverage(accuracy: Mapping[str, Any]) -> float | None:
    for level, found in (accuracy.get("coverage") or {}).items():
        if math.isclose(float(level), 0.8):
            return float(found["coverage"])
    return None


def method(ev: Evidence, namer: Namer, choice: Choice) -> Section:
    section = Section("Method")
    runs = [len(o.runs) for o in ev.options]
    if runs:
        section.lines.append(f"{sum(runs)} run(s) over {len(ev.options)} option(s); runs of different options share "
                             "seeds (common random numbers), so differences come from the options, not from luck.")
    section.lines.append("Ranges hold the middle 80% of runs (10th to 90th percentile); differences are paired over "
                         "shared seeds, with 95% t intervals.")
    if choice.goal is not None:
        rule = ", ".join(f"{r.measure} {r.op} {r.value:g}" for r in choice.requirements) or "no requirement"
        section.lines.append(f"Decision rule: {choice.goal.direction} {choice.goal.measure} subject to {rule} (on "
                             f"means); options meeting it: {', '.join(choice.feasible) or 'none'}.")
    if ev.options:
        measures = sorted({k for o in ev.options for r in o.runs for k, v in r.outputs.items()
                           if isinstance(v, (int, float)) and not isinstance(v, bool)})
        section.tables.append(Table("Every output", ["Option", *measures],
                                    [[o.label, *[_ranged(namer, m, o) or "—" for m in measures]] for o in ev.options]))
    if ev.validation is not None:
        section.lines += ev.validation.report().splitlines()
    if ev.sweep is not None:
        section.lines += ev.sweep.report().splitlines()
    return section
