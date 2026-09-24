"""The optimiser's part of a report (:class:`~fg_env.analysis.optimise_result.OptimisationResult`): the decision it
recommends, its expected objective and how often each constraint held, whether the choice survived fresh seeds or won
by luck, and what one step either way does."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..analysis.optimise_result import OptimisationResult
from .queue import QueueView, blocks
from .sections import Table
from .words import Namer

__all__ = ["recommendation", "plan_table", "risks", "drivers", "summary"]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BORDERLINE = ("The recommendation is borderline: no requirement clearly fails, but not every one holds with the "
               "confidence asked for; more runs, or a plan with a little more room, would settle it.")
#: A constraint's verdict, and the chosen decision's on fresh seeds, in words.
_MET = {"feasible": "met with {confidence:.0%} confidence",
         "borderline": "only just: not settled with {confidence:.0%} confidence", "infeasible": "not met"}
_FRESH = {"feasible": "every requirement held with confidence", "borderline": "no requirement clearly failed",
          "infeasible": "a requirement clearly failed"}
_GOAL = re.compile(r"^\s*(maximi[sz]e|max|minimi[sz]e|min)\s+(?:(mean|median|p\d{1,2})\s+of\s+)?(.+?)\s*$",
                   re.IGNORECASE)


def _named(text: str, namer: Namer, measures: Sequence[str]) -> str:
    """A constraint or objective with every output called by its reader's name."""
    return _NAME.sub(lambda m: namer.name(m.group(0)) if m.group(0) in measures else m.group(0), text)


def _measure_of(text: str, measures: Sequence[str]) -> str | None:
    return next((token for token in _NAME.findall(text) if token in measures), None)


def _value(namer: Namer, measure: str | None, value: Any) -> str:
    return "n/a" if value is None else namer.value(measure, value) if measure else f"{value:.4g}"


def _plan(opt: OptimisationResult, views: Sequence[QueueView]) -> tuple | None:
    """``(view, staff per interval)`` when the decision is a queue's staffing vector."""
    for view in views:
        name = view.staffing_input()
        plan = (opt.best or {}).get(name) if name else None
        if isinstance(plan, list):
            return view, [int(v) for v in plan]
    return None


def recommendation(opt: OptimisationResult, namer: Namer, views: Sequence[QueueView],
                   measures: Sequence[str]) -> list[str]:
    if opt.best is None or opt.estimates is None:
        return ["The optimiser traced a trade-off between "
                f"{' and '.join(_named(o, namer, measures) for o in opt.objectives)} ({len(opt.frontier)} decisions "
                "where neither can improve without the other getting worse), not one choice."]
    lines = {"feasible": [], "borderline": [_BORDERLINE]}.get(
        opt.verdict, ["No decision tried meets every constraint; the closest is below."])
    found = _plan(opt, views)
    lines.append(found[0].plan_text(found[1]) if found else
                 "Set " + ", ".join(f"{name.replace('_', ' ')} to {value}" for name, value in opt.best.items()) + ".")
    for row in opt.estimates["objectives"]:
        match = _GOAL.match(row["objective"])
        measure = _measure_of(row["objective"], measures)
        what = namer.name(measure) if measure else (match.group(3) if match else row["objective"])
        spread = row.get("low") is not None and row["low"] != row["high"]
        interval = (f" (95% CI {_value(namer, measure, row['low'])}–{_value(namer, measure, row['high'])})" if spread
                    else "")
        lines.append(f"Expected {what}: {_value(namer, measure, row['value'])}{interval}, over {row['n']} runs the "
                     "search did not use.")
    lines += [_constraint(row, namer, measures, found[0] if found else None) for row in opt.estimates["constraints"]]
    holdout = opt.holdout
    if holdout:
        fresh = holdout["best"]["objectives"][0]
        measure = _measure_of(fresh["objective"], measures)
        held = _FRESH[holdout["best"]["verdict"]]
        lines.append(f"Checked again on {holdout['seeds']} fresh seeds: "
                     f"{namer.name(measure) if measure else 'objective'} {_value(namer, measure, fresh['value'])}; "
                     f"{held}.")
    return lines


def _constraint(row: Mapping[str, Any], namer: Namer, measures: Sequence[str], view: QueueView | None) -> str:
    text = _named(row["constraint"], namer, measures)
    verdict = _MET[row["verdict"]].format(confidence=row["confidence"])
    if row["value"] is None:
        return f"{text.capitalize()}: no value."
    if "keys_total" in row:
        binding = row.get("binding") or []
        if view is not None and all(key.isdigit() for key in binding):
            binding = [view.when(int(key), int(key)) for key in binding]
        tightest = f"; tightest: {', '.join(binding)}" if binding else ""
        return (f"{text.capitalize()}: holds for {row['keys_holding']} of {row['keys_total']} "
                f"({row['keys_needed']} needed) — {verdict}{tightest}.")
    if "share_needed" in row:
        interval = f" (95% CI {row['low']:.0%}–{row['high']:.0%})" if row.get("low") is not None else ""
        return f"{text.capitalize()}: held in {row['value']:.0%} of runs{interval} — {verdict}."
    measure = _measure_of(row["constraint"], measures)
    return f"{text.capitalize()}: {row.get('stat', 'mean')} {_value(namer, measure, row['value'])} — {verdict}."


def plan_table(opt: OptimisationResult, views: Sequence[QueueView]) -> Table | None:
    found = _plan(opt, views)
    if found is None:
        return None
    view, staff = found
    return Table("Staffing plan", ["When", view.server_word.capitalize()],
                 [[view.when(a, b), str(count)] for a, b, count in blocks(staff)])


def risks(opt: OptimisationResult, namer: Namer, measures: Sequence[str]) -> list[str]:
    out = []
    holdout = opt.holdout or {}
    if holdout.get("seed_luck"):
        out.append("The choice may have won by luck: "
                   + "; ".join(_named(r, namer, measures) for r in holdout["reasons"])
                   + ". Trust the fresh seeds, and search again with more runs.")
    for text in holdout.get("short_within_noise", []):
        out.append(f"On fresh seeds {_named(text, namer, measures)} fell short, within noise.")
    if holdout.get("runner_up") and not holdout.get("still_wins", True) and not holdout.get("seed_luck"):
        out.append("The runner-up did as well on fresh seeds: the two are close.")
    if opt.best is not None and opt.verdict == "borderline":
        out.append("The recommendation is borderline: a fresh set of days could put a requirement just short.")
    elif opt.best is not None and opt.verdict == "infeasible":
        out.append("No decision tried met every constraint.")
    return out


def drivers(opt: OptimisationResult, namer: Namer, measures: Sequence[str]) -> list[str]:
    """What one step either way from a feasible choice breaks (the constraints that make it the choice)."""
    out: list[str] = []
    if not opt.feasible:
        return out
    for row in opt.sensitivity:
        name = row["decision"].replace("_", " ")
        broken = [c["constraint"] for c in row.get("constraints", []) if c["verdict"] != "feasible"]
        if broken:
            out.append(f"One step {row['direction']} in {name} no longer meets "
                       f"{', '.join(_named(c, namer, measures) for c in broken)} with confidence.")
    return out


def summary(opt: OptimisationResult) -> list[str]:
    """The optimiser's own account, for the analyst."""
    return [line for line in opt.report().splitlines() if line.strip()]


def as_dict(opt: OptimisationResult) -> dict[str, Any]:
    holdout = opt.holdout or {}
    return {"decision": opt.best, "feasible": opt.feasible, "verdict": opt.verdict, "estimates": opt.estimates,
            "fresh_seeds": holdout.get("best"), "seed_luck": bool(holdout.get("seed_luck")),
            "reasons": holdout.get("reasons", [])}
