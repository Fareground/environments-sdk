"""The oracle compares independently numbered event views without losing hidden-field checks."""
from dataclasses import fields, replace

import pytest
from expr_dual import same

from fg_env.world.parts import LogEvent


def event():
    return LogEvent(7, 2, "record", "decision", "a", ("a",), {"amount": 1.0}, "trade").numbered(1)


def test_independent_viewer_event_copies_compare_by_every_field():
    left, right = event(), event()
    assert left is not right and left != right
    assert same([left], [right])
    assert left.key == right.key == 7


@pytest.mark.parametrize("field", [field.name for field in fields(LogEvent)])
def test_every_event_field_difference_is_detected(field):
    left = event()
    right = replace(left, **{field: object()})
    assert not same(left, right)


def test_event_data_keeps_exact_nested_types_and_float_representation():
    left = event()
    assert not same(left, replace(left, data={"amount": 1}))
    assert not same(replace(left, data={"amount": 0.0}), replace(left, data={"amount": -0.0}))
