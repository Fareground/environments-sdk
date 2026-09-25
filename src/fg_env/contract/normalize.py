"""Earlier contract forms rewritten into the current ones, so a contract written for an earlier release still loads.

Each rule rewrites one construct wherever it finds its earlier form and leaves the current form alone, so a rule is
idempotent and a document mixing both forms works. :func:`normalize` applies every rule in order and returns the
current form with one note per rewrite (``fg-env migrate`` shows them and can write the result back)."""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from .base import CONTRACT_VERSION

__all__ = ["normalize", "rule", "RULES"]

#: A rule rewrites ``data`` in place and returns a note for each rewrite it made ("path: what became what").
Rule = Callable[[dict[str, Any]], list[str]]
RULES: list[Rule] = []
#: The version of the language the rules rewrite from.
EARLIER_VERSION = "1"


def rule(fn: Rule) -> Rule:
    """Register ``fn`` as a normalization rule (see :func:`_rules` for the order they run in)."""
    RULES.append(fn)
    return fn


def normalize(data: Any) -> tuple[Any, list[str]]:
    """``data`` in the current contract form, and the notes of every rewrite; anything but an object is returned as
    is (the parser reports it)."""
    if not isinstance(data, Mapping):
        return data, []
    out: dict[str, Any] = copy.deepcopy(dict(data))
    notes: list[str] = []
    for fn in _rules():
        notes.extend(fn(out))
    notes.extend(_arm_patches(out))
    if out.get("fg_env") == EARLIER_VERSION:
        out["fg_env"] = CONTRACT_VERSION
        notes.append(f"fg_env: '{EARLIER_VERSION}' → '{CONTRACT_VERSION}'")
    return out, notes


def _arm_patches(data: dict[str, Any]) -> list[str]:
    """Each arm's patch is a contract fragment: every rule is applied to it on its own, after the rules that read a
    patch beside the contract it patches (the patch is merged into the rewritten contract)."""
    arms = data.get("arms")
    if not isinstance(arms, dict):
        return []
    notes = []
    for name, arm in arms.items():
        patch = arm.get("patch") if isinstance(arm, dict) else None
        if isinstance(patch, dict) and patch:
            arm["patch"], found = normalize(patch)
            notes += [f"arms.{name}.patch.{note}" for note in found]
    return notes


#: The modules holding the rules, in the order their rules run: state first (a macro may make any section), then the
#: sections that became mechanisms, then happenings and time.
_MODULES = ("normalize_state", "normalize_mechanisms", "normalize_happenings")


def _rules() -> list[Rule]:
    """Every rule, module by module in :data:`_MODULES` order and in registration order within one. The rules live one
    module per part of the language, which registers them when it is first imported (here, not at load: they import
    :func:`rule` from this module), so the order does not depend on which module something imported first."""
    from . import normalize_happenings, normalize_mechanisms, normalize_state  # noqa: F401

    position = {f"{__package__}.{name}": index for index, name in enumerate(_MODULES)}
    return sorted(RULES, key=lambda fn: position[fn.__module__])
