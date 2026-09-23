"""``FG_ENV_EXPR_ORACLE=1 pytest tests``: run the whole suite with every expression evaluated twice — by the
reference evaluator and by the compiled language — failing any test in which the two differ (see expr_dual.py)."""
import os

import pytest

if os.environ.get("FG_ENV_EXPR_ORACLE"):
    from expr_dual import MISMATCHES, dual_evaluation

    @pytest.fixture(autouse=True)
    def _expressions_evaluated_both_ways():
        start = len(MISMATCHES)
        with dual_evaluation():
            yield
        assert MISMATCHES[start:] == []
