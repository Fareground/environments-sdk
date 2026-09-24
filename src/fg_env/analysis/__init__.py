"""Analysis: turn a contract and its runs into findings, reports and readable records.

    from fg_env import analysis

    grid = analysis.sweep("shop.json", {"price": [8, 10, 12]}, runs=20)
    print(analysis.report(grid, audience="owner"))

* ``sweep``, ``sensitivity``, ``calibrate``, ``optimise`` — outputs across inputs, what drives them, inputs fitted to
  data, the best decision under constraints.
* ``validate``, ``score``, ``backtest``, ``precision`` — forecasts and runs checked against actual values.
* ``behavior_checks``, ``highlights``, ``narrative``, ``drivers``, ``compare``, ``chain`` — broken parts found by
  playing, the notable moments of a run, what separates outcomes, side-by-side results, contracts in sequence.
* ``fit_patterns``, ``decompose`` — a contract's world patterns fitted to history; one pattern split into its parts.
* ``report`` — any of these results as short sentences and tables a manager can act on.
* ``describe`` — an ODD document and game metadata derived from the contract.
* ``trace`` — a recorded run read turn by turn, and replayed offline.
"""
from ..describe import Description, describe
from ..report import Report, report
from ..trace import Trace, trace
from .backtest import BacktestResult, PrecisionResult, backtest, precision
from .calibrate import CalibrationResult, calibrate
from .checks import CheckReport, Finding, behavior_checks
from .compare import ChainResult, Comparison, chain, compare
from .decompose import Decomposition, decompose
from .drivers import Driver, DriversResult, drivers
from .facts import STATISTICS, statistic
from .fit import FitResult, fit_patterns
from .highlights import Highlight, highlights, narrative
from .optimise import OptimisationResult, optimise
from .runner import AnalysisError
from .scoring import (
    brier,
    brier_multiclass,
    crps,
    crps_ensemble,
    ece,
    interval_coverage,
    log_loss,
    log_loss_multiclass,
    murphy,
    reliability,
    score,
    skill_score,
)
from .sensitivity import SensitivityResult, sensitivity
from .sweep import SweepResult, sweep
from .validate import ValidationResult, validate

__all__ = [
    "fit_patterns", "FitResult", "decompose", "Decomposition", "report", "Report", "describe", "Description",
    "trace", "Trace",
    "sweep", "SweepResult", "sensitivity", "SensitivityResult", "calibrate", "CalibrationResult",
    "score", "brier", "brier_multiclass", "log_loss", "log_loss_multiclass", "crps", "crps_ensemble",
    "interval_coverage", "reliability", "ece", "murphy", "skill_score", "backtest", "BacktestResult",
    "precision", "PrecisionResult", "behavior_checks", "CheckReport", "Finding", "highlights", "narrative",
    "Highlight", "drivers", "DriversResult", "Driver", "compare", "Comparison", "chain", "ChainResult",
    "statistic", "STATISTICS", "AnalysisError", "validate", "ValidationResult", "optimise", "OptimisationResult",
]
