"""fg-env: the Environment SDK for LLM agents. A JSON contract describes an environment; the engine runs it.

    import fg_env

    issues = fg_env.check("shop.json")                      # every problem with its path and a fix
    result = fg_env.run("shop.json", {"shopper": "policy:thrifty"}, seed=1)
    print(result.summary())

    env = fg_env.load("shop.json", seed=7)                  # full control: preview, run, snapshot, fork
    exp = fg_env.experiment("shop.json", runs=20, arms=["control", "promo"])

``fg_env.guide("authoring")`` is the start page, for humans and authoring agents; ``fg_env.guide()`` maps every part;
``fg_env.new("auction", "my_auction.json")`` writes a cookbook recipe to start from (``guide("cookbook")``);
``fg_env.migrate(contract)`` rewrites a contract written for an earlier release in the current form.
``fg_env.author(brief, "anthropic:<model>")`` has a model write, check and run one from a plain-language brief.

The core lives here; everything else is one subpackage away: ``fg_env.analysis`` (sweeps, calibration,
optimisation, validation, reports, traces), ``fg_env.rl`` (games, Gym and PettingZoo, tournaments, evaluation),
``fg_env.engines`` (the engine catalog), ``fg_env.personas`` (cohort sampling) and ``fg_env.participants``
(random, policy and LLM participants).
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# Every built-in expression function is registered first, before any module could compile or run an expression.
from . import stdlib  # noqa: F401

if TYPE_CHECKING:
    from . import analysis, engines, participants, personas, rl
    from .api import check, expand, load, migrate, parse, run
    from .authoring.author import author
    from .authoring.scaffold import new
    from .contract import Contract
    from .copying.branch import Branch
    from .copying.forks import fork
    from .errors import ContractError, FatalRunError, InputError, InvariantViolation, Issue, RunError, SnapshotError
    from .experiments.experiment import ExperimentResult, experiment
    from .guides import guide, schema
    from .runtime.env import Env
    from .runtime.measure import RunResult
    from .runtime.session import ToolResult, Wake

#: The module each public name is defined in (``""``: the name is a subpackage). Each loads on first use, so
#: ``import fg_env`` stays fast and a script pays only for what it touches.
_WHERE = {
    **dict.fromkeys(("load", "run", "check", "parse", "expand", "migrate"), ".api"),
    **dict.fromkeys(("experiment", "ExperimentResult"), ".experiments.experiment"),
    "fork": ".copying.forks", "Branch": ".copying.branch", "guide": ".guides", "schema": ".guides",
    "new": ".authoring.scaffold", "author": ".authoring.author", "Env": ".runtime.env", "Contract": ".contract",
    "RunResult": ".runtime.measure", "Wake": ".runtime.session", "ToolResult": ".runtime.session",
    **dict.fromkeys(("Issue", "ContractError", "InputError", "RunError", "InvariantViolation", "FatalRunError",
                     "SnapshotError"), ".errors"),
    **dict.fromkeys(("participants", "analysis", "rl", "engines", "personas"), ""),
}


def __getattr__(name: str) -> Any:
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            value: Any = version("fg-env")
        except PackageNotFoundError:  # a source checkout on PYTHONPATH, not installed
            value = "0+unknown"
    elif name in _WHERE:
        value = (getattr(importlib.import_module(_WHERE[name], __name__), name) if _WHERE[name]
                 else importlib.import_module(f".{name}", __name__))
    else:
        raise AttributeError(f"module 'fg_env' has no attribute '{name}'")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})


__all__ = [
    "__version__",
    "load",
    "run",
    "check",
    "parse",
    "expand",
    "migrate",
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
    "participants",
    "analysis",
    "rl",
    "engines",
    "personas",
]
