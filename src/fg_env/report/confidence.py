"""How sure a decision rule's pick is: whether it beats the next best option by more than noise, and whether it meets
each requirement with room or only just. A pick within noise of the next best is told as a tie, never as a
recommendation, with whatever else clearly separates the two."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..analysis.stats import estimate
from .evidence import Choice, Evidence, Option, Requirement
from .noise import Paired, paired
from .words import Namer

__all__ = ["Confidence", "assess", "label", "interval"]

#: How a requirement that holds reads: service level is "above" 80%.
_SIDE = {">=": "above", ">": "above", "<=": "below", "<": "below"}


def label(option: Option, start: bool = False) -> str:
    """An option as a sentence names it: its description (lower-cased mid-sentence unless it starts with an acronym)
    or its arm label."""
    text = option.description or ("the baseline" if option.label == "baseline" else option.label)
    if start:
        return text[:1].upper() + text[1:]
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def interval(namer: Namer, measure: str, low: float | None, high: float | None) -> str:
    return "no interval from one run" if low is None or high is None else \
        f"95% CI {namer.change(measure, low)} to {namer.change(measure, high)}"


@dataclass
class Confidence:
    """The pick's margin over the next best option that meets the rule, and what else separates them."""

    runner_up: Option | None = None
    margin: Paired | None = None
    #: Clear differences between the pick and the runner-up on the other measures an owner reads.
    others: list[Paired] = field(default_factory=list)

    @property
    def tied(self) -> bool:
        return self.margin is not None and self.margin.within_noise

    def to_dict(self) -> dict[str, Any]:
        return {"runner_up": self.runner_up.label if self.runner_up else None,
                "margin": self.margin.to_dict() if self.margin else None, "within_noise": self.tied}


def assess(ev: Evidence, choice: Choice, measures: Sequence[str]) -> Confidence:
    """The pick against the best of the other options meeting the rule (the goal's paired difference, and the other
    measures' clear ones)."""
    best, goal = choice.best, choice.goal
    if best is None or goal is None:
        return Confidence()
    rivals = [o for o in ev.options if o is not best and o.label in choice.feasible and o.values(goal.measure)]
    if not rivals:
        return Confidence()

    def mean(option: Option) -> float:
        values = option.values(goal.measure)
        return sum(values) / len(values)

    runner = min(rivals, key=mean) if goal.direction == "min" else max(rivals, key=mean)
    others = [found for m in measures if m != goal.measure
              for found in [paired(best, runner, m)] if found is not None and not found.within_noise]
    return Confidence(runner, paired(best, runner, goal.measure), others)


def lines(ev: Evidence, choice: Choice, sure: Confidence, namer: Namer) -> list[str]:
    """The pick in words: "Choose …" with its margin, or a tie with what else separates the two; then how sure each
    requirement is."""
    best, goal = choice.best, choice.goal
    if best is None or goal is None:
        return []
    out: list[str] = []
    margin, runner = sure.margin, sure.runner_up
    if runner is None:
        out.append(f"Choose {label(best)}." + (" It is the only option that meets the requirement."
                                               if choice.requirements and len(ev.options) > 1 else ""))
    elif margin is None or margin.within_noise:
        out.append(f"{label(best, start=True)} and {label(runner)} are within noise on {namer.name(goal.measure)}"
                   + (f": {namer.change(goal.measure, margin.mean)} "
                      f"({interval(namer, goal.measure, margin.low, margin.high)})"
                      if margin is not None else "") + ", so the rule cannot pick between them.")
        if sure.others:
            told = "; ".join(f"{namer.name(p.measure)} {namer.change(p.measure, p.mean)} "
                             f"({interval(namer, p.measure, p.low, p.high)})" for p in sure.others)
            out.append(f"Decide between them on something else. Against {label(runner)}, {label(best)} has {told}.")
        else:
            out.append("Decide between them on something else: nothing measured here separates them.")
    else:
        out.append(f"Choose {label(best)}.")
        better = "lower" if goal.direction == "min" else "higher"
        out.append(f"How sure: its {namer.name(goal.measure)} is {better} than with {label(runner)} by "
                   f"{namer.value(goal.measure, abs(margin.mean))} "
                   f"({interval(namer, goal.measure, margin.low, margin.high)}, {margin.n} paired runs).")
    out += [text for r in choice.requirements for text in [_requirement(best, r, namer, sure.tied)] if text]
    return out


def _requirement(option: Option, requirement: Requirement, namer: Namer, named: bool) -> str | None:
    """Whether the pick's mean meets a requirement with room, or only just (its 95% interval crosses the bound). After a
    tie, which option it is about is said outright."""
    found = estimate(option.values(requirement.measure))
    if found.mean is None or found.low is None or found.high is None:
        return None
    name, bound = namer.name(requirement.measure), namer.value(requirement.measure, requirement.value)
    side = _SIDE[requirement.op]
    low, high = found.low, found.high
    if namer.is_share(requirement.measure):
        low, high = max(0.0, low), min(1.0, high)
    spread = f"mean {namer.value(requirement.measure, found.mean)}, 95% CI " \
             f"{namer.value(requirement.measure, low)}–{namer.value(requirement.measure, high)}"
    subject = f"With {label(option)}, {name}" if named else f"Its {name}"
    if requirement.met(found.low) and requirement.met(found.high):
        return f"{subject} is clearly {side} {bound} ({spread})."
    return f"{subject} is {side} {bound} only just: the interval crosses it ({spread})."
