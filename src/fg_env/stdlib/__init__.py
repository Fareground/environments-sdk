"""Every built-in expression function: the core (:mod:`.core`: collections, the world, math and text), the standard
library (dates, distributions, linear algebra, lists, statistics, scoring, text, tables, puzzles), space and assets.

Each function is declared where it is implemented, with :func:`fg_env.expr.function`. This package is the one place
that lists the modules holding them; ``fg_env`` imports it when it loads, so every function is registered before any
expression runs. The modules import the expression language, never the other way round.
"""
from . import (  # noqa: F401  (each module registers the functions it declares)
    assets,
    core,
    dates,
    dists,
    linalg,
    lists,
    mathx,
    puzzles,
    scoring,
    sets,
    space,
    stats,
    strings,
    tables,
    words,
)
