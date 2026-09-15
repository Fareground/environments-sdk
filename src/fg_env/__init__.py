"""fg-env: the Environment SDK for LLM agents. A JSON contract describes an environment; the engine runs it.

    import fg_env

    issues = fg_env.check("shop.json")                      # every problem with its path and a fix
    result = fg_env.run("shop.json", {"shopper": "policy:thrifty"}, seed=1)
    print(result.summary())

    env = fg_env.load("shop.json", seed=7)                  # full control: preview, run, snapshot, fork
    exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"])

``fg_env.guide()`` is the core authoring guide, mapping every other part (``fg_env.guide("all")`` is everything);
``fg_env.new("game", "my_game.json")`` writes a ready-to-run contract to start from. The earlier template-based kernel API lives in
:mod:`fg_env.legacy`.
"""
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("fg-env")
except _PackageNotFoundError:  # a source checkout on PYTHONPATH, not installed
    __version__ = "0+unknown"

# Environment SDK public API. Everything a user needs is importable from ``fg_env``.
from .sdk.api import check, expand, load, parse, run
from .sdk.branch import Branch
from .sdk.fork import fork
from .sdk.game import Game, GameState, game
from .sdk.game.conformance import conformance
from .sdk.game.pettingzoo import pettingzoo_aec, pettingzoo_parallel
from .sdk.game.playthrough import playthrough
from .sdk.gym import GymEnv, gym
from .sdk.contract import Contract
from .sdk.errors import ContractError, InputError, InvariantViolation, Issue, RunError, SnapshotError
from .sdk.experiment import ExperimentResult, experiment
from .sdk.guide import guide, schema
from .sdk.scaffold import new
from .sdk.measure import RunResult
from .sdk.runtime import Env
from .sdk.session import ToolResult, Wake
from .sdk import participants
from .sdk import analysis
from .sdk.analysis import (backtest, behavior_checks, calibrate, chain, compare, drivers, highlights, narrative,
                           optimise, precision, score, sensitivity, sweep, validate)
from .sdk.tournament import tournament
from .sdk.describe import describe
from .sdk.trace import trace
from .sdk.evaluate import evaluate

__all__ = [
    "__version__",
    "load",
    "run",
    "check",
    "parse",
    "expand",
    "experiment",
    "fork",
    "game",
    "gym",
    "Branch",
    "Game",
    "GameState",
    "GymEnv",
    "guide",
    "schema",
    "new",
    "Env",
    "Contract",
    "RunResult",
    "ExperimentResult",
    "Wake",
    "ToolResult",
    "Issue",
    "ContractError",
    "InputError",
    "RunError",
    "InvariantViolation",
    "SnapshotError",
    "participants",
    "analysis",
    "sweep",
    "sensitivity",
    "calibrate",
    "optimise",
    "score",
    "backtest",
    "validate",
    "precision",
    "behavior_checks",
    "highlights",
    "narrative",
    "drivers",
    "compare",
    "chain",
    "tournament",
    "describe",
    "trace",
    "evaluate",
    "conformance",
    "playthrough",
    "pettingzoo_aec",
    "pettingzoo_parallel",
]
