"""Versioned, reusable behavioral engines bundled with :mod:`fg_env`.

This package deliberately contains engines, not finished environments, scenario
presets, or Arena games.  A builder clones an available engine and supplies the
roles, population, subject matter, and rules for its custom scenario.
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
