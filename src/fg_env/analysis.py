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
from .sdk import analysis as _analysis
from .sdk.analysis import *  # noqa: F403 - the analysis toolkit, whole
from .sdk.describe import Description, describe
from .sdk.patterns.decompose import Decomposition, decompose
from .sdk.patterns.fit import FitResult, fit_patterns
from .sdk.report import Report, report
from .sdk.trace import Trace, trace

__all__ = ["fit_patterns", "FitResult", "decompose", "Decomposition", "report", "Report", "describe",
           "Description", "trace", "Trace"]
__all__ += _analysis.__all__
