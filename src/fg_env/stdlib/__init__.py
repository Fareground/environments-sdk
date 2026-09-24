"""The expression standard library: pure (or seeded) functions every contract can call.

Importing the package registers every function; ``fg_env.stdlib.core`` imports it, so the
functions are available wherever expressions are.
"""
from . import dates, dists, linalg, lists, mathx, puzzles, scoring, sets, stats, strings, tables, words  # noqa: F401  (register on import)
