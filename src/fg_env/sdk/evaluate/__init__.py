"""Evaluation: how well a focal participant does among background agents, against a baseline on the same seeds.

* :func:`evaluate` — every scenario of a suite, in every mode (the share of seats the focal participant takes),
  run with the focal participant and again with the baseline in the same seats on identical seeds.
* :class:`EvaluationResult` — per scenario and mode, per tag, per mode, held-out versus in-sample and overall:
  the focal score per focal seat, the baseline's, their paired difference with a 95% interval, and cost.
"""
from .play import evaluate
from .result import EvaluationResult

__all__ = ["evaluate", "EvaluationResult"]
