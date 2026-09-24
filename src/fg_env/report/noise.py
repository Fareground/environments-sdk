"""Whether two options really differ: paired differences over the runs they share a seed with, and the words for a
difference too small to choose on.

Runs of different arms and sweep cells share seeds (common random numbers), so the difference in run *i* compares
like with like. A difference is within noise when its 95% interval holds zero, or when it is smaller than a sliver of
the outcome itself — a real but trivial gap is no reason to pick one option over the other either.
"""
from __future__ import annotations


import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..analysis.stats import estimate
from ..runtime.measure import _usable_output
from .evidence import Option

__all__ = ["Paired", "paired", "MEANINGFUL_SHARE"]

#: A difference smaller than this share of the outcome is not worth choosing on, however sure it is.
MEANINGFUL_SHARE = 0.01


@dataclass(frozen=True)
class Paired:
    """``mine − theirs`` on one measure, over the seeds both options ran."""

    measure: str
    n: int
    mean: float
    low: Optional[float]
    high: Optional[float]
    #: The size of the outcome the difference is judged against (the larger mean of the two, in absolute value).
    scale: float

    @property
    def fixed(self) -> bool:
        """The same difference in every run: the options themselves differ (a staffing cost), not their luck."""
        return self.low is not None and self.high is not None and math.isclose(self.low, self.high)

    @property
    def uncertain(self) -> bool:
        """Too few pairs for an interval, or an interval that holds zero."""
        return self.low is None or self.high is None or (self.low <= 0 <= self.high and not self.fixed)

    @property
    def trivial(self) -> bool:
        return abs(self.mean) < MEANINGFUL_SHARE * self.scale

    @property
    def within_noise(self) -> bool:
        return self.uncertain or self.trivial

    def to_dict(self) -> Dict[str, Any]:
        return {"measure": self.measure, "n": self.n, "difference": self.mean, "ci95": [self.low, self.high],
                "within_noise": self.within_noise}


def _by_seed(option: Option, measure: str) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for run in option.runs:
        if not _usable_output(run, measure):
            continue
        value = run.outputs.get(measure)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[run.seed] = float(value)
    return out


def paired(mine: Option, theirs: Option, measure: str) -> Optional[Paired]:
    """The paired difference ``mine − theirs`` on ``measure`` (``None`` when no seed has a value in both)."""
    a, b = _by_seed(mine, measure), _by_seed(theirs, measure)
    seeds: List[int] = [seed for seed in a if seed in b]
    if not seeds:
        return None
    found = estimate([a[seed] - b[seed] for seed in seeds])
    assert found.mean is not None
    scale = max(abs(math.fsum(a[s] for s in seeds)), abs(math.fsum(b[s] for s in seeds))) / len(seeds)
    return Paired(measure, found.n, found.mean, found.low, found.high, scale)
