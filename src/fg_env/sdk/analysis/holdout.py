"""Holding cases out: a train/test split or k-fold cross-validation over named cases, deterministic from a seed.

A fit judged only on the cases it was fitted to flatters itself; the error on cases it never saw is the
honest estimate of how it will do on the next one. Splits are drawn from the analysis seed, so the same
seed always holds out the same cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..seeds import SeedTree
from .stats import is_number

__all__ = ["Split", "case_names", "splits"]


@dataclass(frozen=True)
class Split:
    """One way to divide the cases: fit on ``train``, measure on ``test`` (indices into the case list)."""

    label: str
    train: Tuple[int, ...]
    test: Tuple[int, ...]


def case_names(cases: Sequence[Mapping[str, Any]]) -> List[str]:
    """Each case's ``name`` (default ``case <n>``); names must be unique so a held-out case can be named."""
    names = [str(case.get("name", f"case {i + 1}")) for i, case in enumerate(cases)]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"case names must be unique; repeated: {', '.join(repeated)}")
    return names


def splits(names: Sequence[str], *, test: Any = None, folds: Optional[int] = None, seed: int = 0) -> List[Split]:
    """The splits asked for: none (no ``test`` or ``folds``), one train/test split, or ``folds`` folds.

    ``test`` is a share of the cases between 0 and 1 (rounded, at least one case on each side) or a list
    of case names or positions. ``folds`` deals the shuffled cases into that many near-equal groups; each
    group is held out once.
    """
    if test is not None and folds is not None:
        raise ValueError("give test or folds, not both")
    if test is None and folds is None:
        return []
    count = len(names)
    if count < 2:
        raise ValueError("holding cases out needs at least two cases")
    order = list(range(count))
    SeedTree(seed).rng("holdout").shuffle(order)
    if folds is not None:
        if isinstance(folds, bool) or not isinstance(folds, int) or not 2 <= folds <= count:
            raise ValueError(f"folds must be a whole number from 2 to the number of cases ({count}), got {folds!r}")
        groups = [sorted(order[k::folds]) for k in range(folds)]
        return [Split(f"fold {k + 1} of {folds}", tuple(i for i in range(count) if i not in group), tuple(group))
                for k, group in enumerate(groups)]
    if is_number(test) and not isinstance(test, int):
        if not 0 < test < 1:
            raise ValueError(f"test as a share must be between 0 and 1, got {test}")
        held = sorted(order[:min(count - 1, max(1, round(test * count)))])
    elif isinstance(test, (list, tuple)) and test:
        held = sorted({_position(item, names) for item in test})
        if len(held) == count:
            raise ValueError("every case is held out; leave at least one case to fit on")
    else:
        raise ValueError(f"test must be a share of the cases between 0 and 1, or a list of case names, got {test!r}")
    return [Split("test", tuple(i for i in range(count) if i not in held), tuple(held))]


def _position(item: Any, names: Sequence[str]) -> int:
    if isinstance(item, str) and item in names:
        return names.index(item)
    if isinstance(item, int) and not isinstance(item, bool) and 0 <= item < len(names):
        return item
    raise ValueError(f"held-out case {item!r} is not a case name or position (cases: {', '.join(names)})")
