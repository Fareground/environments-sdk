"""Typed errors. Every message says where the problem is and what to change."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .measure import RunResult

__all__ = ["Issue", "ContractError", "InputError", "RunError", "InvariantViolation", "FatalRunError",
           "SnapshotError"]


@dataclass(frozen=True)
class Issue:
    """One problem found in a contract or its inputs."""

    path: str
    message: str
    fix: Optional[str] = None
    severity: str = "error"

    def __str__(self) -> str:
        text = f"{self.path}: {self.message}"
        if self.fix:
            text += f" → {self.fix}"
        return text if self.severity == "error" else f"[{self.severity}] {text}"

    def to_dict(self) -> dict:
        out = {"path": self.path, "message": self.message, "severity": self.severity}
        if self.fix:
            out["fix"] = self.fix
        return out


class ContractError(ValueError):
    """The contract is invalid. ``issues`` lists every error found (not just the first)."""

    def __init__(self, issues: List[Issue], title: str = "contract is invalid"):
        self.issues = [i for i in issues if i.severity == "error"]
        self.warnings = [i for i in issues if i.severity != "error"]
        lines = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"{title} ({len(self.issues)} error(s)):\n{lines}")


class InputError(ContractError):
    """Run inputs do not match the contract's declared inputs."""

    def __init__(self, issues: List[Issue]):
        super().__init__(issues, title="inputs are invalid")


class RunError(RuntimeError):
    """A run could not continue. ``path`` names the contract element that failed."""

    #: The failed run, when :func:`fg_env.run` raised this (outputs so far, statistics, events).
    result: Optional["RunResult"] = None

    def __init__(self, message: str, path: Optional[str] = None):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class InvariantViolation(RunError):
    """A declared invariant stopped holding. Broken by an agent's action, the action is refused and undone; broken by
    anything else, the run fails closed. ``why`` is the invariant's own reason (empty when it gives none)."""

    def __init__(self, message: str, path: Optional[str] = None, why: str = ""):
        self.why = why
        super().__init__(message, path)


class FatalRunError(RunError):
    """A failure outside the contract's rules — a host failed or cannot be asked, a replay stopped matching its
    recording, a mechanism's code crashed, the world reached its ceiling of living entities. Unlike a rule failing
    inside an agent's action, it fails the run wherever it happens."""


class SnapshotError(ValueError):
    """A snapshot cannot be restored into this contract."""
