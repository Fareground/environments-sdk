"""Self-description: an ODD-protocol document and game metadata, both derived from a contract.

``fg_env.describe(contract)`` returns a :class:`Description` with ``markdown`` (ODD: purpose, entities and
state variables, process and scheduling, design concepts, initialisation, inputs, submodels) and ``metadata``
(dynamics, chance mode, information, utility, players, length, action space, observations, concepts, and
the evidence for each). ``check_claims`` compares properties a contract asserts about itself with the
derivation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from ..api import ContractLike, apply_arm, default_data_dir, load, parse
from ..errors import InputError, Issue, RunError
from ..expr import ExprError
from .claims import CLAIMS, claim_issues
from .metadata import game_metadata
from .odd import odd_markdown

__all__ = ["describe", "Description", "check_claims", "CLAIMS"]


@dataclass(frozen=True)
class Description:
    name: str
    markdown: str
    metadata: Dict[str, Any]

    def __str__(self) -> str:
        return self.markdown

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "metadata": self.metadata, "markdown": self.markdown}

    def info(self) -> str:
        """The metadata as plain text, each property followed by its evidence."""
        m, evidence = self.metadata, self.metadata["evidence"]
        length, space = m["max_game_length"], m["action_space"]
        players = "unknown" if m["num_players"] is None else str(m["num_players"])
        players += f" (from {_known(m['min_players'])} to {_known(m['max_players'])})"
        rows = [
            ("dynamics", m["dynamics"], "dynamics"),
            ("chance", m["chance_mode"] + (f" (during {' and '.join(m['chance_during'])})" if m["chance_during"] else ""),
             "chance_mode"),
            ("information", m["information"], "information"),
            ("utility", m["utility"], "utility"),
            ("players", players, "players"),
            ("length", f"{_known(length['rounds'])} rounds at most, "
                       + ("decisions not bounded" if length["decisions"] is None else f"{length['decisions']} decisions at most"),
             "max_game_length"),
            ("action space", space["kind"] + (f", {space['size']} distinct actions" if space["size"] is not None else ""),
             "action_space"),
        ]
        lines = [m["name"]]
        for label, value, key in rows:
            lines.append(f"{label}: {value}")
            lines += [f"  - {line}" for line in evidence.get(key, [])]
        views = "; ".join(f"{kind}: {', '.join(names) or 'none'}" for kind, names in m["observations"]["views"].items())
        lines.append(f"observations: text (views — {views or 'none'})")
        lines.append("concepts: " + (", ".join(m["concepts"]) or "none"))
        return "\n".join(lines)


def _known(value: Any) -> str:
    return "unknown" if value is None else str(value)


def describe(contract: ContractLike, *, inputs: Optional[Mapping[str, Any]] = None, arm: Optional[str] = None,
             data_dir: Any = None) -> Description:
    """Describe ``contract`` (with ``inputs`` and ``arm`` applied). Counts that need the built world (players,
    entity choices, rounds given by an expression) come from building it once with seed 0; when it cannot be
    built — a required input is missing, say — they are reported as unknown with the reason."""
    folder = default_data_dir(contract, data_dir)
    parsed = parse(contract)
    probe: Any = None
    error: Optional[str] = None
    try:
        probe = load(parsed, inputs=dict(inputs or {}), seed=0, arm=arm, data_dir=folder)
        used = probe.contract
    except (InputError, RunError, ExprError) as exc:
        error = "; ".join(str(issue) for issue in exc.issues) if isinstance(exc, InputError) else str(exc)
        used = apply_arm(parsed, arm) if arm is not None else parsed
    metadata = game_metadata(used, probe, error)
    return Description(used.name, odd_markdown(used, metadata), metadata)


def check_claims(contract: ContractLike, claims: Mapping[str, Any], *, inputs: Optional[Mapping[str, Any]] = None,
                 arm: Optional[str] = None, data_dir: Any = None, path: str = "game") -> List[Issue]:
    """Errors for claims the contract contradicts, warnings for claims it cannot settle (see :data:`CLAIMS`)."""
    return claim_issues(describe(contract, inputs=inputs, arm=arm, data_dir=data_dir).metadata, claims, path)
