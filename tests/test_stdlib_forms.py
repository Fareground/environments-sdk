"""Escaped quotes inside template expressions, and $stdev over a plain list."""
import math

import pytest

from fg_env.expr import ExprError, Scope, compile_expr
from fg_env.template import compile_template


def test_an_escaped_quote_inside_a_template_expression_does_not_end_the_string():
    assert compile_template(r"{'it\'s'} fine", None).render(Scope()) == "it's fine"
    assert compile_template(r'{"a \"b\" c"}', None).render(Scope()) == 'a "b" c'
    assert compile_template(r"{'back\\'} slash", None).render(Scope()) == "back\\ slash"


def test_stdev_takes_a_plain_list_like_sum_and_avg():
    assert math.isclose(compile_expr("$stdev([1, 2, 3, 4])")(Scope()), 1.2909944487358056)
    assert compile_expr("$stdev([5])")(Scope()) is None
    assert compile_expr("$stdev([1, null, 3])")(Scope()) == compile_expr("$stdev([1, 3])")(Scope())
    with pytest.raises(ExprError):
        compile_expr("$stdev(['a', 'b'])")(Scope())
