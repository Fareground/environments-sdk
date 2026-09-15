"""The expression standard library: pure (or seeded) functions every contract can call.

Importing the package registers every function; ``fg_env.sdk.functions`` imports it, so the
functions are available wherever expressions are.
"""
from . import dists, lists, mathx, puzzles, scoring, sets, stats, strings, words  # noqa: F401  (register on import)
