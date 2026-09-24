"""The engine catalog: versioned, runnable starters bundled with :mod:`fg_env`.

An engine is a runnable starter for one kind of human interaction (a market, a negotiation, a vote …): a complete
contract with coded participants that runs as cloned. A builder clones one and makes it their own — its topic, roles,
people, inputs and rules.
"""
from .catalog import (
    EngineCatalog,
    EngineNotFound,
    EngineSpec,
    EngineUnavailable,
    catalog,
    clone,
    get,
    list_engines,
    load,
)

__all__ = [
    "EngineCatalog",
    "EngineNotFound",
    "EngineUnavailable",
    "EngineSpec",
    "catalog",
    "list_engines",
    "get",
    "clone",
    "load",
]
