"""Shared persona/cohort sampling for every environment engine.

Sampling creates participants; it is not itself a behavioural engine.  The
functions here deliberately operate on ordinary mappings so an application can
feed census records, research panels, authored personas or fixed participants
without coupling the SDK to a particular database.
"""
from __future__ import annotations

import copy
import hashlib
import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


Constraint = Union[Any, Sequence[Any], Callable[[Any], bool]]


@dataclass(frozen=True)
class SamplingProvenance:
    source: str
    source_version: Optional[str]
    seed: int
    run: int
    resampled: bool
    requested: int
    selected: int
    pool_size: int
    constraints: Dict[str, Any]
    group_by: Optional[str]
    weight_field: Optional[str]
    fixed_ids: Tuple[str, ...]
    sampled_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PersonaSample:
    people: Tuple[Dict[str, Any], ...]
    provenance: SamplingProvenance

    def records(self) -> List[Dict[str, Any]]:
        """Return mutable copies suitable for assigning roles/models per run."""
        return [copy.deepcopy(person) for person in self.people]

    def to_dict(self) -> Dict[str, Any]:
        return {"people": self.records(), "provenance": self.provenance.to_dict()}


def _read(record: Mapping[str, Any], field: str) -> Any:
    value: Any = record
    for part in field.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _matches(record: Mapping[str, Any], constraints: Mapping[str, Constraint]) -> bool:
    for field, expected in constraints.items():
        value = _read(record, field)
        if callable(expected):
            if not expected(value):
                return False
        elif isinstance(expected, (set, frozenset, tuple, list)) and not isinstance(expected, str):
            if value not in expected:
                return False
        elif value != expected:
            return False
    return True


def _identity(record: Mapping[str, Any], id_field: str) -> str:
    value = _read(record, id_field)
    if value in (None, ""):
        raise ValueError(f"persona record is missing nonempty {id_field!r}")
    return str(value)


def _derived_seed(seed: int, run: int, resample: bool) -> int:
    if not resample:
        return int(seed)
    digest = hashlib.sha256(f"fg-env-personas:{seed}:{run}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _weighted_order(items: List[Tuple[str, List[Dict[str, Any]], float]], rng: random.Random) -> List[Tuple[str, List[Dict[str, Any]], float]]:
    keyed = []
    for item in items:
        weight = item[2]
        if not math.isfinite(weight) or weight <= 0:
            continue
        keyed.append((math.log(max(rng.random(), 1e-15)) / weight, item))
    keyed.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _key, item in keyed]


def sample_records(records: Iterable[Mapping[str, Any]], *, size: int, seed: int = 0, run: int = 0,
                   resample: bool = True, constraints: Optional[Mapping[str, Constraint]] = None,
                   fixed: Optional[Iterable[Mapping[str, Any]]] = None, id_field: str = "id",
                   group_by: Optional[str] = None, weight_field: Optional[str] = None,
                   source: str = "records", source_version: Optional[str] = None) -> PersonaSample:
    """Sample role-neutral personas with replacement disabled and full provenance.

    ``fixed`` participants are always first and count toward ``size``.  ``group_by``
    keeps related records together in draw order (Market uses household ids).  A
    group may be truncated at the requested cohort size; no record is duplicated.
    ``run`` only changes the draw when ``resample`` is true, allowing experiments
    to choose fixed-cohort repetition or a fresh cohort per run explicitly.
    """
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError("size must be a positive integer")
    if isinstance(run, bool) or not isinstance(run, int) or run < 0:
        raise ValueError("run must be a nonnegative integer")
    constraints = dict(constraints or {})
    pool = [copy.deepcopy(dict(record)) for record in records if _matches(record, constraints)]
    fixed_people = [copy.deepcopy(dict(record)) for record in (fixed or ())]
    fixed_ids = [_identity(record, id_field) for record in fixed_people]
    if len(set(fixed_ids)) != len(fixed_ids):
        raise ValueError("fixed personas contain duplicate ids")
    if len(fixed_people) > size:
        raise ValueError("fixed personas exceed requested size")
    excluded = set(fixed_ids)
    candidates = [record for record in pool if _identity(record, id_field) not in excluded]
    ids = [_identity(record, id_field) for record in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("persona pool contains duplicate ids")
    needed = size - len(fixed_people)
    if len(candidates) < needed:
        raise ValueError(f"only {len(candidates) + len(fixed_people)} matching personas are available; {size} requested")

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in candidates:
        key = str(_read(record, group_by)) if group_by else _identity(record, id_field)
        grouped.setdefault(key, []).append(record)
    items = []
    for key, members in grouped.items():
        if weight_field:
            weights = [float(_read(member, weight_field) or 0) for member in members]
            weight = sum(weights) / len(weights)
        else:
            weight = 1.0
        items.append((key, members, weight))
    actual_seed = _derived_seed(seed, run, resample)
    ordered = _weighted_order(items, random.Random(actual_seed))
    sampled = list(fixed_people)
    for _key, members, _weight in ordered:
        for member in members:
            if len(sampled) >= size:
                break
            sampled.append(copy.deepcopy(member))
        if len(sampled) >= size:
            break
    if len(sampled) != size:
        raise ValueError(f"sampling produced {len(sampled)} personas; {size} requested")
    sampled_ids = tuple(_identity(record, id_field) for record in sampled)
    provenance = SamplingProvenance(
        source=source,
        source_version=source_version,
        seed=int(seed),
        run=run,
        resampled=bool(resample),
        requested=size,
        selected=len(sampled),
        pool_size=len(pool),
        constraints={key: value for key, value in constraints.items() if not callable(value)},
        group_by=group_by,
        weight_field=weight_field,
        fixed_ids=tuple(fixed_ids),
        sampled_ids=sampled_ids,
    )
    return PersonaSample(tuple(sampled), provenance)


def assign_labels(records: Iterable[Mapping[str, Any]], labels: Sequence[Tuple[str, float]], *,
                  field: str = "role", seed: int = 0) -> List[Dict[str, Any]]:
    """Assign labels by proportional shares using largest remainder, then shuffle."""
    people = [copy.deepcopy(dict(record)) for record in records]
    if not labels:
        raise ValueError("at least one label/share is required")
    shares = [max(0.0, float(share)) for _label, share in labels]
    total = sum(shares)
    if total <= 0:
        raise ValueError("label shares must contain a positive value")
    exact = [len(people) * share / total for share in shares]
    counts = [int(value) for value in exact]
    remaining = len(people) - sum(counts)
    order = sorted(range(len(labels)), key=lambda index: exact[index] - counts[index], reverse=True)
    for index in order[:remaining]:
        counts[index] += 1
    assigned = [label for (label, _share), count in zip(labels, counts) for _ in range(count)]
    random.Random(seed).shuffle(assigned)
    for person, label in zip(people, assigned):
        person[field] = label
    return people


__all__ = [
    "Constraint",
    "PersonaSample",
    "SamplingProvenance",
    "sample_records",
    "assign_labels",
]
