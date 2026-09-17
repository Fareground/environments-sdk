"""Versioned, reusable engine starters bundled with :mod:`fg_env`.

The catalogue separates reusable engines from their scenario/game presets.
Native contract starters are the preferred base for new work.  Existing
Fareground templates remain runnable through the compatibility runtime while
they are migrated one engine at a time.
"""
from .catalog import (
    EngineCatalog,
    EngineNotFound,
    EnginePreset,
    EngineSpec,
    catalog,
    clone,
    get,
    list_engines,
    load,
)

__all__ = [
    "EngineCatalog",
    "EngineNotFound",
    "EnginePreset",
    "EngineSpec",
    "catalog",
    "list_engines",
    "get",
    "clone",
    "load",
]
