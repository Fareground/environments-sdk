"""Analysis: turn runs of a contract into trustworthy findings.

Pure analysis over contracts and run results through the public API. Deterministic given seeds:
every analysis runs its variants on common random numbers, derived exactly as
:func:`fg_env.experiment` derives them.

* :func:`sweep` — outputs across a grid or Latin hypercube of inputs, with main effects.
* :func:`sensitivity` — rank inputs by influence (one-at-a-time elasticities, Morris, Sobol-lite).
* :func:`calibrate` — fit inputs to target numbers, paths, distributions or stylized facts.
* :func:`score` and :func:`backtest` — verify forecasts against outcomes.
* :func:`precision` — add runs until an estimate is precise enough.
* :func:`behavior_checks` — find broken parts of an environment by playing it.
* :func:`highlights` and :func:`narrative` — the notable moments of one run.
* :func:`drivers` — what separates runs where an outcome happens from runs where it does not.
* :func:`compare` and :func:`chain` — side-by-side results; one contract's outputs into another's inputs.
"""
from .backtest import BacktestResult, PrecisionResult, backtest, precision
from .calibrate import CalibrationResult, calibrate
from .checks import CheckReport, Finding, behavior_checks
from .compare import ChainResult, Comparison, chain, compare
from .drivers import Driver, DriversResult, drivers
from .facts import STATISTICS, statistic
from .highlights import Highlight, highlights, narrative
from .runner import AnalysisError
from .scoring import (
    brier, brier_multiclass, crps, crps_ensemble, ece, interval_coverage, log_loss, log_loss_multiclass, murphy,
    reliability, score, skill_score,
)
from .sensitivity import SensitivityResult, sensitivity
from .sweep import SweepResult, sweep

__all__ = [
    "sweep", "SweepResult", "sensitivity", "SensitivityResult", "calibrate", "CalibrationResult",
    "score", "brier", "brier_multiclass", "log_loss", "log_loss_multiclass", "crps", "crps_ensemble",
    "interval_coverage", "reliability", "ece", "murphy", "skill_score", "backtest", "BacktestResult",
    "precision", "PrecisionResult", "behavior_checks", "CheckReport", "Finding", "highlights", "narrative",
    "Highlight", "drivers", "DriversResult", "Driver", "compare", "Comparison", "chain", "ChainResult",
    "statistic", "STATISTICS", "AnalysisError",
]
