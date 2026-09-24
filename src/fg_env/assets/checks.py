"""Static checks for assets: the `assets` section, asset properties and record fields, `attach` rules and `file`
parameters. Whether the files exist and match is checked when the contract is loaded from its folder."""
from __future__ import annotations

import re
from typing import Any

from .kinds import HARD_MAX_BYTES, KINDS

__all__ = ["check_assets"]

_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def check_assets(checker: Any, base: frozenset[str]) -> None:
    contract = checker.c
    for name, spec in contract.assets.items():
        path = f"assets.{name}"
        if not _ID.match(name):
            checker.error(path, "asset names are letters, digits, _, - and .", "rename it")
        if (spec.file is None) == (spec.folder is None):
            checker.error(path, "give exactly one of `file` or `folder`", 'e.g. "file": "evidence/contract.pdf"')
        if spec.type is not None and spec.type not in KINDS:
            checker.error(f"{path}.type", f"unknown asset type '{spec.type}'", f"use one of: {', '.join(KINDS)}")
        if spec.max_bytes is not None and spec.max_bytes > HARD_MAX_BYTES:
            checker.error(f"{path}.max_bytes", f"is above the ceiling of {HARD_MAX_BYTES:,} bytes")
        if spec.describe is not None and not spec.describe.strip():
            checker.error(f"{path}.describe", "names no host", 'e.g. "describe": "vision"')
    _literal_assets(checker)
    agents = set(checker.agents)
    if contract.brief.attach is not None:
        checker.expr(contract.brief.attach, "brief.attach", base | {"actor"}, {"actor": agents})
    for name, view in contract.views.items():
        if view.attach is None:
            continue
        path = f"views.{name}.attach"
        its: set[str] = set(contract.subtypes(view.of)) if view.of in contract.types else set()
        checker.expr(view.attach, path, base | {"actor", "it", "i"}, {"actor": agents, "it": its})
    for name, action in contract.actions.items():
        path = f"actions.{name}"
        by = [action.by] if isinstance(action.by, str) else action.by
        if action.attach is not None:
            checker.expr(action.attach, f"{path}.attach", base | {"actor", "params"},
                         {"actor": {t for t in by if t in contract.types}}, action.params)
        for pname, param in action.params.items():
            ppath = f"{path}.params.{pname}"
            if param.type != "file":
                if param.kinds is not None or param.max_bytes is not None:
                    checker.error(ppath, "kinds and max_bytes apply to file parameters")
                continue
            for kind in param.kinds or ():
                if kind not in KINDS:
                    checker.error(f"{ppath}.kinds", f"unknown asset type '{kind}'", f"use: {', '.join(KINDS)}")
            if param.max_bytes is not None and not 1 <= param.max_bytes <= HARD_MAX_BYTES:
                checker.error(f"{ppath}.max_bytes", f"must be from 1 to {HARD_MAX_BYTES:,}")


def _literal_assets(checker: Any) -> None:
    """Asset properties whose literal value names no declared asset (data-file assets are checked at load)."""
    contract = checker.c
    declared = set(contract.assets)
    folders = {name for name, spec in contract.assets.items() if spec.folder is not None}
    columns = any(kind == "asset" for spec in contract.inputs.values() for kind in (spec.columns or {}).values())

    def check(value: Any, path: str) -> None:
        if not isinstance(value, str) or "$" in value or "{" in value or columns:
            return
        if value in declared or value.split("/", 1)[0] in folders:
            return
        hint = checker._suggest(value, declared)
        checker.error(path, f"'{value}' is not a declared asset", hint or f"assets: {', '.join(declared) or 'none'}")

    for type_name, spec in contract.types.items():
        for prop, prop_spec in spec.props.items():
            if prop_spec.type == "asset":
                check(prop_spec.default, f"types.{type_name}.props.{prop}.default")
    for entity_id, entity in contract.entities.items():
        props = contract.props_of(entity.type) if entity.type in contract.types else {}
        for prop, value in entity.props.items():
            if prop in props and props[prop].type == "asset":
                check(value, f"entities.{entity_id}.props.{prop}")
