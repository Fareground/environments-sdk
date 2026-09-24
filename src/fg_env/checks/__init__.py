"""Static contract checking: every problem found at once, each with its path and a fix.

Beyond structure, the checker compiles every expression and template, confirms that
referenced types, properties, params, records, relations, stages, views, metrics and
inputs exist, and that each expression only uses roots available where it is written.

The checker is built in parts: what they share — the issues found, and checking one expression, template or value
(:mod:`.core`) — then what agents may learn (:mod:`.privacy`), effects (:mod:`.effects`), the world model
(:mod:`.world`), actions, stages and views (:mod:`.actions`), and events, policies, measures, defs and arms
(:mod:`.rules`).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from .. import contract as C
from ..assets.checks import check_assets
from ..contract import Contract
from ..contract.normalize import normalize
from ..contract.parse_errors import validation_issues
from ..errors import ContractError, Issue
from ..host.common import raw_model_ids
from ..patterns.check import check_patterns
from ..runtime.returns import check_game
from .actions import ActionChecks
from .inventory import check_inventory
from .roots import BASE
from .rules import RuleChecks
from .scans import check_scans
from .state import check_feeds, check_physics_state, check_relation_fields
from .world import WorldChecks

__all__ = ["parse_contract", "check_contract"]


def parse_contract(data: Any) -> Contract:
    """Validate structure. Raises :class:`ContractError` with every structural problem."""
    if isinstance(data, Contract):
        return data
    if not isinstance(data, Mapping):
        raise ContractError([Issue("(contract)", f"a contract is a JSON object, got {type(data).__name__}")])
    from ..mechanisms import expand_mechanisms

    source, notes = normalize(data)
    expanded, mechanism_issues = expand_mechanisms(source)
    if mechanism_issues:
        raise ContractError(_dedupe(mechanism_issues))
    expanded = normalize(expanded)[0]  # what mechanisms generate in an earlier form: not the author's to rewrite
    try:
        contract = Contract.model_validate(expanded)
        contract._source = source
        contract._notes = notes
        return contract
    except ValidationError as exc:
        raise ContractError(_dedupe(validation_issues(exc))) from None


def _dedupe(issues: Iterable[Issue]) -> list[Issue]:
    seen, out = set(), []
    for issue in issues:
        key = (issue.path, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def check_contract(contract: Contract) -> list[Issue]:
    """All semantic errors and warnings (errors first)."""
    checker = _Checker(contract)
    checker.run()
    issues = _dedupe(checker.issues)
    return [i for i in issues if i.severity == "error"] + [i for i in issues if i.severity != "error"]


class _Checker(RuleChecks, ActionChecks, WorldChecks):
    """The whole check: every part, run in order."""

    # -- sections -----------------------------------------------------------------------

    def run(self) -> None:
        c = self.c
        if c.fg_env != C.CONTRACT_VERSION:
            self.error("fg_env", f"unsupported contract version '{c.fg_env}'", f"use \"{C.CONTRACT_VERSION}\"")
        self._inputs()
        self._brief()
        self._clock_space()
        self._types_and_world()
        self._keyword_names()
        self._entities()
        check_inventory(self)
        self._relations()
        check_relation_fields(self, BASE)
        self._physics()
        check_physics_state(self, BASE)
        self._records()
        check_feeds(self, BASE)
        check_patterns(self, BASE)
        self._actions()
        self._stages()
        self._views()
        self._secret_subtypes()
        self._events()
        self._policies()
        self._measure()
        self._arms()
        self._defs()
        check_game(self)
        check_scans(self)
        check_assets(self, BASE)
        from ..mechanisms import authored_slips, separate_turns

        self.issues.extend(separate_turns(c._source or {}))
        self.issues.extend(authored_slips(c._source or {}))
        self.issues.extend(raw_model_ids(c._source or {}))
