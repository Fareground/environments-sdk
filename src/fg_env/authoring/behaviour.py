"""How a contract behaved in its test runs, and what a revision's behaviour lost against an earlier one's.

A revision that guts an environment — a rule rewritten to do nothing, a game cut short, a score made constant, a text
agents read emptied — can be written in endlessly many ways, so it is found by what the test runs did, not by how the
revision reads. :func:`profile` records, over all of a revision's test runs: the rounds they played, the rules whose
effects fired and the actions agents took, the outputs and scores that varied, and for every view shown and every
agent type's brief the longest text it rendered and whether it ever read differently (its template values changed).
:func:`collapsed` names each dimension on which a new revision's profile fell from the kept one's: something that
happened never does, something that varied is constant, fewer rounds are played, a text is gone or cut to almost
nothing. Each must be confirmed like a removal.
"""
from __future__ import annotations

from typing import Any

from ..contract import Contract
from .findings import Seen

__all__ = ["profile", "collapsed"]

#: A text this long or longer that a revision cuts to less than a :data:`_CUT_TO`-th of it is emptied.
_SUBSTANTIAL = 40
_CUT_TO = 8


def profile(contract: Contract, seen: Seen, fired: list[str], flat: list[str]) -> dict[str, Any]:
    """The behaviour of ``contract``'s test runs (``seen``; ``fired``: the rules whose effects fired, as parts;
    ``flat``: the outputs that came out the same in every run), as JSON data."""
    runs = seen.runs
    taken = {event["data"].get("action") for run in runs for event in run.events
             if event.get("kind") == "action" and event.get("data", {}).get("success", True)}
    scores = {round(float(value), 9) for run in runs for value in (run.returns or {}).values()}
    return {
        "rounds": max((run.rounds for run in runs), default=0),
        "fired": sorted(fired),
        "taken": sorted(str(name) for name in taken if name),
        "varied": sorted(name for name in contract.outputs if name not in flat),
        "scores": len(scores) > 1,
        "views": {name: [length, varied] for name, (length, _, varied) in sorted(seen.views.items())},
        "briefs": {kind: [length, varied] for kind, (length, _, varied) in sorted(seen.briefs.items())},
    }


def collapsed(old: dict[str, Any], new: dict[str, Any], parts: set[str]) -> list[str]:
    """Each dimension on which ``new`` fell from ``old`` (see the module docstring), in full; ``parts`` are the parts
    the new revision still has (a rule, action, output or view it removed is reported as removed, not here)."""
    if not old or not new:
        return []
    found = []
    if new["rounds"] < old["rounds"]:
        found.append(f"rounds played (every test run now plays at most {new['rounds']}, where one played "
                     f"{old['rounds']})")
    found += [f"{rule} (its effects changed nothing in any test run)"
              for rule in old["fired"] if rule in parts and rule not in new["fired"]]
    found += [f"actions.{name} (taken in no test run, where agents took it before)"
              for name in old["taken"] if f"actions.{name}" in parts and name not in new["taken"]
              and f"actions.{name}" not in old["fired"]]
    found += [f"outputs.{name} (came out the same in every test run, where it varied before)"
              for name in old["varied"] if f"outputs.{name}" in parts and name not in new["varied"]]
    if old["scores"] and not new["scores"]:
        found.append("scores (every seat scored the same in every test run, where they differed before)")
    found += _texts("views", old["views"], new["views"], parts, "shown to no agent in any test run")
    found += _texts("briefs", old["briefs"], new["briefs"], None, "no agent of the type read one")
    return found


def _texts(section: str, old: dict[str, list[Any]], new: dict[str, list[Any]], parts: set[str] | None,
           missing: str) -> list[str]:
    found = []
    for name, (length, varied) in old.items():
        path = f"{section}.{name}" if section == "views" else f"brief of {name}"
        if parts is not None and path not in parts:
            continue
        if name not in new:
            found.append(f"{path} ({missing}, where it was before)")
            continue
        now, changes = new[name]
        if length >= _SUBSTANTIAL and now * _CUT_TO < length:
            found.append(f"{path} (cut from {length} to {now} characters)")
        elif varied and not changes:
            found.append(f"{path} (reads the same every time, where its values changed before)")
    return found
