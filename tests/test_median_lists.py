"""List and entity statistics use the same median semantics."""
import statistics

import pytest

import fg_env
from fg_env.expr import ExprError, Scope, evaluate


@pytest.mark.parametrize("values", [[], [None], [7], [9, 1, 5], [8, 2, 6, 4],
                                     [None, 9, 1, None, 5], [-8, -2, -6, -4]])
def test_direct_list_median_matches_the_independent_reference(values):
    present = [v for v in values if v is not None]
    expected = statistics.median(present) if present else None
    original = list(values)
    scope = Scope({"values": values})
    assert evaluate("$median($values)", scope) == expected
    assert evaluate("$median($values, $it)", scope) == expected
    assert scope.vars["values"] == original


@pytest.mark.parametrize("values", [[True, 1], ["2", 1], [{"price": 2}], 4])
def test_direct_median_does_not_coerce_invalid_measurements(values):
    with pytest.raises(ExprError):
        evaluate("$median($values)", Scope({"values": values}))


def test_agent_can_measure_a_round_series_or_a_filtered_population():
    c = {"name": "Cohort summary", "clock": {"rounds": 3}, "types": {"buyer": {"props": {"spend": 0}}},
         "entities": {f"buyer_{i}": {"type": "buyer", "props": {"spend": value}}
                      for i, value in enumerate([0, 4, 8, 100])},
         "outputs": {"sales": {"expr": "$round * 10", "series": True}, "median_sales": "$median($series.sales)",
                     "paying_customer_spend": "$median(buyer, $it.spend, $it.spend > 0)"}}
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    result = fg_env.run(c, seed=1)
    assert result.status == "completed", result.error
    assert result.outputs == {"sales": 30, "median_sales": 20, "paying_customer_spend": 8}
