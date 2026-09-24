"""Normalization rules for state, outcomes and reuse: earlier sections rewritten into their current homes.

* ``metrics`` → ``outputs`` with ``series``; ``$metrics.x`` → ``$outputs.x``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .normalize import rule

__all__: list[str] = []


def _strings(value: Any, change: Callable[[str], str]) -> Any:
    """``value`` with ``change`` applied to every text in it (keys included)."""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [_strings(item, change) for item in value]
    if isinstance(value, dict):
        return {change(key) if isinstance(key, str) else key: _strings(item, change) for key, item in value.items()}
    return value


def _replace_all(data: dict[str, Any], pattern: re.Pattern[str], replacement: str) -> bool:
    """Apply ``pattern`` → ``replacement`` to every text in ``data`` (in place); whether anything changed."""
    changed = False

    def change(text: str) -> str:
        nonlocal changed
        new = pattern.sub(replacement, text)
        changed = changed or new != text
        return new

    rewritten = _strings(data, change)
    if changed:
        data.clear()
        data.update(rewritten)
    return changed


# -- metrics → outputs.series ---------------------------------------------------------------------------------------

_METRICS_ROOT = re.compile(r"\$metrics\b")


def _expr_of(spec: Any) -> Any:
    return spec.get("expr") if isinstance(spec, dict) else spec


@rule
def metrics_into_outputs(data: dict[str, Any]) -> list[str]:
    """``metrics: {m: e}`` → ``outputs: {m: {expr: e, series: true}}``. An output of the same name keeps its own
    expression and samples the metric's (``series: e``); one that only read the metric (``$metrics.m``) becomes the
    metric, sampled."""
    notes = []
    metrics = data.pop("metrics", None)
    if isinstance(metrics, dict) and metrics:
        outputs = data.setdefault("outputs", {})
        if not isinstance(outputs, dict):  # the parser reports the malformed section
            data["metrics"] = metrics
            return []
        for name, metric in metrics.items():
            spec = dict(metric) if isinstance(metric, dict) else {"expr": metric}
            output = outputs.get(name)
            if output is None:
                outputs[name] = {**spec, "series": True}
                notes.append(f"metrics.{name}: now outputs.{name} with series: true")
                continue
            merged = dict(output) if isinstance(output, dict) else {"expr": output}
            if _expr_of(output) in (spec.get("expr"), f"$metrics.{name}"):
                merged.update(expr=spec.get("expr"), series=True)
            else:
                merged["series"] = spec.get("expr")
            for key in ("unit", "description"):
                if spec.get(key) and not merged.get(key):
                    merged[key] = spec[key]
            outputs[name] = merged
            notes.append(f"metrics.{name}: merged into outputs.{name} as its series")
    elif metrics is not None and not isinstance(metrics, dict):
        data["metrics"] = metrics  # malformed: left for the parser
    if _replace_all(data, _METRICS_ROOT, "$outputs"):
        notes.append("$metrics.x: now $outputs.x (a series output's latest sample)")
    return notes
