"""How a contract behaved in its test runs, and what a revision's behaviour lost against an earlier one's.

A revision that guts an environment — a rule rewritten to do nothing, a game cut short, a score made constant, a text
agents read emptied — can be written in endlessly many ways, so it is found by what the test runs did, not by how the
revision reads. :func:`profile` records, over all of a revision's test runs: the rounds they played, the rules whose
effects fired and the actions agents took, how many turns random agents took an action in and how many different
values they chose for each argument, the outputs (among random agents' runs: an idle agent's leave them at their
defaults, which would pass for variety) and scores that varied, and for every view shown and every agent type's brief
the longest text it rendered and whether it ever read differently (its template values changed).
:func:`collapsed` names each dimension on which a new revision's profile fell from the kept one's: something that
happened never does, something that varied is constant, fewer rounds are played, agents act in far fewer turns or
their choices of an argument narrow to one value, a text is gone or cut to almost nothing. Each must be confirmed like
a removal.
"""
from __future__ import annotations

import json
from typing import Any

from ..contract import Contract
from .findings import Seen

__all__ = ["profile", "collapsed"]

#: Turns with an action falling below a :data:`_FEWER`-th of what they were is agents' play cut away.
_FEWER = 2
#: A text this long or longer that a revision cuts to less than a :data:`_CUT_TO`-th of it is emptied.
_SUBSTANTIAL = 40
_CUT_TO = 8


def profile(contract: Contract, seen: Seen, fired: list[str], flat: list[str]) -> dict[str, Any]:
    """The behaviour of ``contract``'s test runs (``seen``; ``fired``: the rules whose effects fired, as parts;
    ``flat``: the outputs that came out the same in every run), as JSON data."""
    runs = seen.runs
    taken = {event["data"].get("action") for run in runs for event in run.events
             if event.get("kind") == "action" and event.get("data", {}).get("success", True)}
    explored = [run for run, kind in zip(runs, seen.kinds) if kind == "random"] or runs
    acted: set[tuple[int, int, Any]] = set()
    chosen: dict[str, set[str]] = {}
    for number, run in enumerate(explored):
        for event in run.events:
            data = event.get("data") or {}
            if event.get("kind") != "action" or not data.get("success", True):
                continue
            acted.add((number, event.get("round", 0), event.get("actor")))
            for pname, value in (data.get("params") or {}).items():
                chosen.setdefault(f"{data.get('action')}.{pname}", set()).add(json.dumps(value, sort_keys=True))
    scores = {round(float(value), 9) for run in runs for value in (run.returns or {}).values()}
    return {
        "rounds": max((run.rounds for run in runs), default=0),
        "fired": sorted(fired),
        "taken": sorted(str(name) for name in taken if name),
        "acted": len(acted),
        "choices": {key: len(values) for key, values in sorted(chosen.items())},
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
    if new.get("acted", 0) * _FEWER < old.get("acted", 0):
        found.append(f"turns with an action (random agents acted in {new.get('acted', 0)} turns of the test runs, "
                     f"where they acted in {old.get('acted', 0)})")
    found += [f"actions.{key} (random agents chose one value in every test run, where they chose {count})"
              for key, count in old.get("choices", {}).items()
              if count > 1 and new.get("choices", {}).get(key) == 1 and f"actions.{key.split('.')[0]}" in parts]
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
