"""What testing a contract shows beyond a problem that stops it (see :func:`~fg_env.authoring.testing.tested`): how
much agents read a turn, measures that come out the same in every test run, and agent types whose brief never says
what they are after. Each is a warning, shown to the author with the verdict."""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from ..contract import Contract
from ..errors import Issue
from ..runtime.measure import RunResult

__all__ = ["Seen", "ROUND_TOKENS", "flat_measures", "aimless"]

#: Prompt tokens (every agent turn's brief and update) one round may send models before a test warns: past it, a run
#: with model agents costs a lot per round — usually each agent reading a list of every other one.
ROUND_TOKENS = 100_000
#: Characters per token, as :class:`~fg_env.runtime.facts.Stats` estimates them.
_CHARS_PER_TOKEN = 4
#: Words that say what an agent is after: a goal, a win, something to make as large or small as it can.
_GOAL = re.compile(r"\b(goals?|aims?|objectives?|purpose|wins?|winn(er|ing)|maximi[sz]e|minimi[sz]e|tr(y|ies|ying)|"
                   r"wants?|hopes?|your (job|task|mission)|succeed|success|score|reward|the (most|best|highest|"
                   r"lowest|fewest|least|largest|smallest|biggest))\b", re.IGNORECASE)


class Seen:
    """What the test runs showed beyond their problems: the runs that finished, how much agents read a turn (their
    brief and update), and the rules whose effects fired and the views shown in any of them."""

    def __init__(self) -> None:
        self.runs: list[RunResult] = []
        #: ``actions.<name>`` and ``events[<i>]`` whose effects fired (see :attr:`EffectRunner.fired`).
        self.fired: set[str] = set()
        #: The views some run showed an agent (see :attr:`Perception.rendered`).
        self.views: set[str] = set()
        self.turns = self.chars = self.most = 0
        self._lock = threading.Lock()  # simultaneous turns read in parallel

    def read(self, chars: int) -> None:
        """Count one turn in which an agent read ``chars`` characters."""
        with self._lock:
            self.turns, self.chars, self.most = self.turns + 1, self.chars + chars, max(self.most, chars)

    @property
    def prompt(self) -> tuple[int, int]:
        """``(average, largest)`` tokens an agent read in a turn."""
        return round(self.chars / max(1, self.turns) / _CHARS_PER_TOKEN), round(self.most / _CHARS_PER_TOKEN)

    def warnings(self, contract: Contract, stubbed: bool = False) -> list[Issue]:
        """Everything this module warns about, for ``contract`` and these runs (``stubbed``: see :func:`flat_measures`).
        """
        return [*self._large(), *flat_measures(contract, self.runs, stubbed), *aimless(contract)]

    def _large(self) -> list[Issue]:
        rounds = sum(max(1, run.rounds) for run in self.runs)
        per_round = self.chars / _CHARS_PER_TOKEN / max(1, rounds)
        if per_round <= ROUND_TOKENS:
            return []
        turns = self.turns / max(1, rounds)
        return [Issue("views", f"each round sends models about {per_round:,.0f} prompt tokens ({turns:,.0f} agent "
                               f"turns of ~{self.prompt[0]:,} tokens each), a costly run with model agents",
                      "show each agent less: a view that summarises (counts, a few nearest or best) rather than "
                      "listing every entity, or fewer agents woken each round", "warning")]


def flat_measures(contract: Contract, runs: list[RunResult], stubbed: bool = False) -> list[Issue]:
    """Outputs and series that came out the same, or empty (null), in every one of ``runs`` (two at least): whatever
    the agents did and whatever luck drew, they measured nothing that changed — a winner no rule decides but a
    tie-break, a sum of what no rule adds to. With ``stubbed`` — the runs' hosts were the SDK's stand-in stubs, which
    answer the same every time — an output read from what a host mechanism answers is left out: it is flat by design."""
    if len(runs) < 2:
        return []
    hosts = [name for name, use in (contract.mechanisms or {}).items()
             if isinstance(use, dict) and use.get("kind") == "host"] if stubbed else []
    names = "|".join(map(re.escape, hosts))
    answered = re.compile(rf"\$records\(\s*(?:{names})\b|\$world\.(?:{names})_") if hosts else None
    found: list[Issue] = []
    for name, spec in contract.outputs.items():
        if answered is not None and answered.search(spec.expr or ""):
            continue
        sampled = spec.series is True  # its result is its last sample: every sample tells more
        found += _flat(f"outputs.{name}", [value for run in runs for value in run.series.get(name, [])] if sampled
                       else [run.outputs.get(name) for run in runs])
    return found


def _flat(path: str, values: list[Any]) -> list[Issue]:
    texts = {json.dumps(value, sort_keys=True, default=str) for value in values}
    if len(texts) != 1:
        return []
    what = "was empty (null)" if values[0] is None else f"came out {texts.pop()}"
    return [Issue(path, f"{what} in every test run (random, idle and edge-value agents)",
                  "if nothing agents or luck do can change it, it measures nothing: make it read what actions and "
                  "rules change; if only play the test agents never made would change it, ignore this", "warning")]


def aimless(contract: Contract) -> list[Issue]:
    """Agent types whose brief — its situation, rules and the roles of the type and its parents — never says what the
    agent is after: a model then plays without a goal."""
    brief = contract.brief
    found: list[Issue] = []
    for name in contract.agent_types():
        if any(contract.is_a(other, name) for other in contract.types if other != name):
            continue  # a parent type: its children's briefs are the ones read
        text = " ".join([brief.situation, brief.rules, *(brief.roles.get(kind, "") for kind in contract.lineage(name))])
        if not _GOAL.search(text):
            found.append(Issue(f"brief.roles.{name}", f"the brief never says what a {name} is trying to achieve",
                               f"say it, e.g. \"Your goal: ...\" in brief.roles.{name}", "warning"))
    return found
