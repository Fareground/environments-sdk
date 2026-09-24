"""Shared plumbing for host mechanisms: type checks, agent lists, clipping."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import Issue
from ..expr import Untrusted
from ..expr.objects import Entity
from ..registry import MechanismError

__all__ = ["NAME", "MODEL_HINT", "type_list", "agents_of", "clip", "ellipsis", "raw_model_ids"]

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
#: What a host mechanism's `model` field is: a name the host may map, never a model it must use.
MODEL_HINT = ("A name for the kind of model wanted (e.g. \"strong\"), which the host maps to one of its own models "
              "(LLMHost(..., models={...})); a host that does not map it uses its own model.")
#: A `model` hint that reads as a provider's model id rather than a name the host maps: a version number, a path or
#: tag, or a provider's model family.
_RAW_MODEL_ID = re.compile(r"\d|[/:]|^(claude|gpt|gemini|llama|mistral|grok|deepseek|qwen|o[1-9])", re.IGNORECASE)

ellipsis = "…"


def raw_model_ids(data: Mapping[str, Any]) -> list[Issue]:
    """Warnings for host mechanisms (``kind`` host or mind) whose `model` hint is a provider's model id: the operator's
    host decides which model answers, and maps a contract's hint only when told to."""
    issues: list[Issue] = []
    uses = data.get("mechanisms")
    for name, use in uses.items() if isinstance(uses, Mapping) else ():
        if isinstance(use, Mapping) and use.get("kind") in ("host", "mind"):
            _raw_models(use, f"mechanisms.{name}", issues)
    return issues


def _raw_models(value: Any, path: str, issues: list[Issue]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "model" and isinstance(item, str) and _RAW_MODEL_ID.search(item):
                issues.append(Issue(f"{path}.model", f"'{item}' reads as a provider's model id, but the host decides "
                                                     "which model answers: it uses its own unless it maps this name",
                                    "name the kind of model wanted (e.g. \"model\": \"strong\") and map it on the "
                                    "host: host.adapters.anthropic(client, \"...\", models={\"strong\": \"...\"}); or "
                                    "leave `model` out", "warning"))
            else:
                _raw_models(item, f"{path}.{key}", issues)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _raw_models(item, f"{path}[{index}]", issues)


def type_list(contract: Mapping[str, Any], value: str | Sequence[str], field: str) -> list[str]:
    """The type names in ``value``, each checked against the contract's types."""
    names = [value] if isinstance(value, str) else list(value)
    types = contract.get("types") or {}
    if not names:
        raise MechanismError("names no type", f"types: {', '.join(types) or 'none'}", field)
    for name in names:
        if name not in types:
            raise MechanismError(f"'{name}' is not a declared type", f"types: {', '.join(types) or 'none'}", field)
    return names


def agents_of(world: Any, types: str | Sequence[str]) -> list[Entity]:
    """Living entities of any of ``types`` (subtypes included), in seat order, each once."""
    kinds = [types] if isinstance(types, str) else list(types)
    return [e for e in world.entities.values() if e.alive and any(world.is_a(e.entity_type, k) for k in kinds)]


def clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, keeping its provenance."""
    if len(text) <= limit:
        return text
    cut = str.__str__(text)[: max(0, limit - 1)] + ellipsis
    return Untrusted(cut) if isinstance(text, Untrusted) else cut


def prop_of(entity: Any, name: str, default: Any = None) -> Any:
    """An entity property as plain data, or ``default`` when it is unset."""
    value = entity.properties.get(name)
    return default if value is None else value
