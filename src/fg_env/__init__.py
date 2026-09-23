"""fg-env: the Environment SDK for LLM agents. A JSON contract describes an environment; the engine runs it.

    import fg_env

    issues = fg_env.check("shop.json")                      # every problem with its path and a fix
    result = fg_env.run("shop.json", {"shopper": "policy:thrifty"}, seed=1)
    print(result.summary())

    env = fg_env.load("shop.json", seed=7)                  # full control: preview, run, snapshot, fork
    exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"])

``fg_env.guide("authoring")`` is the start page for an authoring agent; ``fg_env.guide()`` adds a map of every part;
``fg_env.new("game", "my_game.json")`` writes a ready-to-run contract to start from.
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
from .sdk.patterns.fit import FitResult, fit_patterns
from .sdk.patterns.decompose import Decomposition, decompose
from .sdk.report import Report, report
from .sdk.trace import trace
from .sdk.evaluate import evaluate
from . import engines
from .engines import (
    EngineNotFound,
    EngineUnavailable,
    clone as clone_engine,
    get as get_engine,
    list_engines,
    load as load_engine,
)
from . import personas
from .personas import PersonaSample, SamplingProvenance, assign_labels, sample_records

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
    "fit_patterns",
    "FitResult",
    "decompose",
    "Decomposition",
    "report",
    "Report",
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
    "engines",
    "EngineNotFound",
    "EngineUnavailable",
    "list_engines",
    "get_engine",
    "clone_engine",
    "load_engine",
    "personas",
    "PersonaSample",
    "SamplingProvenance",
    "sample_records",
    "assign_labels",
    "conformance",
    "playthrough",
    "pettingzoo_aec",
    "pettingzoo_parallel",
]
