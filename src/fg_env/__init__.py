"""fg-env: the Environment SDK for LLM agents. A JSON contract describes an environment; the engine runs it.

    import fg_env

    issues = fg_env.check("shop.json")                      # every problem with its path and a fix
    result = fg_env.run("shop.json", {"shopper": "policy:thrifty"}, seed=1)
    print(result.summary())

    env = fg_env.load("shop.json", seed=7)                  # full control: preview, run, snapshot, fork
    exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"])

``fg_env.guide("authoring")`` is the start page for an authoring agent; ``fg_env.guide()`` adds a map of every part;
``fg_env.new("game", "my_game.json")`` writes a ready-to-run contract to start from.
``fg_env.author(brief, "anthropic:<model>")`` has a model write, check and run one from a plain-language brief.

The core lives here; everything else is one subpackage away: ``fg_env.analysis`` (sweeps, calibration,
optimisation, validation, reports, traces), ``fg_env.rl`` (games, Gym and PettingZoo, tournaments, evaluation),
``fg_env.engines`` (the engine catalog), ``fg_env.personas`` (cohort sampling) and ``fg_env.participants``
(random, policy and LLM participants).
"""
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("fg-env")
except _PackageNotFoundError:  # a source checkout on PYTHONPATH, not installed
    __version__ = "0+unknown"

from .api import check, expand, load, parse, run
from .authoring import author
from .branch import Branch
from .contract import Contract
from .errors import ContractError, FatalRunError, InputError, InvariantViolation, Issue, RunError, SnapshotError
from .experiments import ExperimentResult, experiment
from .forks import fork
from .guides import guide, schema
from .measure import RunResult
from .runtime import Env
from .scaffold import new
from .session import ToolResult, Wake
from .engines import clone as clone_engine, list_engines
from . import analysis, engines, participants, personas, rl

__all__ = [
    "__version__",
    "load",
    "run",
    "check",
    "parse",
    "expand",
    "experiment",
    "fork",
    "Branch",
    "guide",
    "schema",
    "new",
    "author",
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
    "FatalRunError",
    "SnapshotError",
    "list_engines",
    "clone_engine",
    "participants",
    "analysis",
    "rl",
    "engines",
    "personas",
]
