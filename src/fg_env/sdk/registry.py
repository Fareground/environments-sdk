"""Extension points: native effect operations and mechanism kinds.

Built-in mechanisms (voting, markets, games, …) register here. An effect op is usable in any
effect list as ``{"<name>": ..., <keys>}``; a mechanism kind is usable in a contract as
``"mechanisms": {"<use name>": {"kind": "<kind>", ...config}}`` and expands into ordinary
contract sections. Both work only through the world's journaled API, so atomic rollback,
snapshots and determinism hold for everything registered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Type

from pydantic import BaseModel

__all__ = ["OpSpec", "MechanismSpec", "MechanismError", "OPS", "MECHANISMS", "effect_op", "mechanism"]


@dataclass(frozen=True)
class OpSpec:
    """A native effect operation."""

    name: str
    #: Every key the op accepts; the op's own name is always accepted.
    keys: Tuple[str, ...]
    #: ``run(runner, effect, vars, where)`` — change the world through ``runner.world``.
    run: Callable[[Any, Dict[str, Any], Dict[str, Any], str], None]
    example: str
    required: Tuple[str, ...] = ()
    #: Keys holding plain names (not expressions), skipped by the expression checker.
    literal: Tuple[str, ...] = ()
    #: Keys holding templates rather than expressions.
    templates: Tuple[str, ...] = ()
    #: Extra static checks: ``check(checker, effect, path)`` returning ``[(path, message, fix), ...]``.
    check: Optional[Callable[[Any, Dict[str, Any], str], list]] = None


@dataclass(frozen=True)
class MechanismSpec:
    """A mechanism kind: validated config in, contract sections out."""

    kind: str
    doc: str
    config: Type[BaseModel]
    #: ``expand(use_name, config, contract_so_far)`` → a fragment of contract sections.
    expand: Callable[[str, Any, Mapping[str, Any]], Dict[str, Any]]
    example: Dict[str, Any] = field(default_factory=dict)


class MechanismError(Exception):
    """A mechanism's config cannot be expanded; the message says what to fix."""

    def __init__(self, message: str, fix: Optional[str] = None, path: str = ""):
        super().__init__(message)
        self.fix = fix
        self.path = path


OPS: Dict[str, OpSpec] = {}
MECHANISMS: Dict[str, MechanismSpec] = {}


def effect_op(name: str, keys: Tuple[str, ...], example: str, *, required: Tuple[str, ...] = (),
              literal: Tuple[str, ...] = (), templates: Tuple[str, ...] = (),
              check: Optional[Callable[[Any, Dict[str, Any], str], list]] = None) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Register a native effect operation."""

    def register(run: Callable[..., None]) -> Callable[..., None]:
        if name in OPS:
            raise ValueError(f"effect op '{name}' is registered twice")
        OPS[name] = OpSpec(name, (name, *keys), run, example, required, literal, templates, check)
        return run

    return register


def mechanism(kind: str, config: Type[BaseModel], doc: str,
              example: Optional[Dict[str, Any]] = None) -> Callable[[Callable[..., Dict[str, Any]]], Callable[..., Dict[str, Any]]]:
    """Register a mechanism kind."""

    def register(expand: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
        if kind in MECHANISMS:
            raise ValueError(f"mechanism kind '{kind}' is registered twice")
        MECHANISMS[kind] = MechanismSpec(kind, doc, config, expand, dict(example or {}))
        return expand

    return register
