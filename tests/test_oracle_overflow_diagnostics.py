"""Both evaluators refuse overflowing fractional arithmetic with the public diagnostic."""
import pytest
from expr_oracle import compile_oracle

from fg_env.expr import ExprError, Scope, compile_expr


@pytest.mark.parametrize("expression", [
    "$round(10 ** 400 * 1.5)", "1.5 * (10 ** 400)", "-(10 ** 400) * 1.5",
])
def test_fractional_overflow_preserves_exact_error_parity(expression):
    errors = []
    for compiler in (compile_oracle, compile_expr):
        with pytest.raises(ExprError) as error:
            compiler(expression)(Scope({}))
        errors.append(str(error.value))
    assert errors[0] == errors[1]
    assert "too large for a fraction" in errors[0]
    assert "OverflowError" not in errors[0]
