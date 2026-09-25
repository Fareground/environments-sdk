"""The ``pattern`` family: one mode per pattern kind (``{"kind": "pattern", "mode": "trend", ...}``), read as
``$pattern.<name>``. The kinds, their configs and how they run are in :mod:`fg_env.patterns`; a pattern adds to the
contract only a metric when it has ``record: true``, and the world property memory patterns keep their state in."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..patterns import catalogue  # noqa: F401  (registers every kind)
from ..patterns.base import KINDS, MEMORY_STATE, PatternConfig
from ..registry import mode

__all__: list[str] = []


def _expand(name: str, cfg: PatternConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    kind = KINDS[cfg.kind]
    fragment: dict[str, Any] = {}
    if cfg.record and not kind.arg_names(cfg):  # a pattern called with arguments has no one value to record
        expr = f"$pattern_values('{name}')" if cfg.keyed else f"$pattern.{name}"
        fragment["metrics"] = {name: {"expr": expr, "description": cfg.description or f"The {cfg.kind} pattern "
                                                                                      f"'{name}'.", "unit": cfg.unit}}
    if kind.shape == "memory":
        fragment["world"] = {MEMORY_STATE: {"type": "map", "default": {},
                                            "description": "State memory patterns carry between rounds (managed by "
                                                           "the engine)."}}
    return fragment


for _name, _kind in KINDS.items():
    mode("pattern", _name, _kind.model, _kind.doc,
         example={key: value for key, value in _kind.example.items() if key != "kind"}, context=_kind.context)(_expand)
