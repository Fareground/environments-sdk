"""Output maps that reject invalid values only when an analysis expression reads them."""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _collections_abc import dict_items, dict_values

from ..runtime.measure import RunResult, _usable_output


class RejectedOutput(Exception):
    """An unavailable measure, kept distinct from repairable expression errors."""


class OutputScope(dict[str, Any]):
    """Keep normal map semantics, including dynamic lookup and lazy expressions.

    Keys and length remain inspectable. Reading an invalid value, including through
    a whole-map operation, refuses the measure instead of hiding or imputing data.
    """

    def __init__(self, result: RunResult):
        super().__init__(result.outputs)
        self._rejected = {name for name in self if not _usable_output(result, name)}

    def _check(self) -> None:
        if self._rejected:
            raise RejectedOutput("expression consumes invalid outputs")

    def __getitem__(self, key: str) -> Any:
        if key in self._rejected:
            raise RejectedOutput(f"expression consumes invalid output '{key}'")
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in self else default

    def __iter__(self) -> Iterator[str]:
        # Ensure copying/unpacking uses mapping lookup, not the raw-dict fast path.
        return super().__iter__()

    def items(self) -> dict_items[str, Any]:
        self._check()
        return super().items()

    def values(self) -> dict_values[str, Any]:
        self._check()
        return super().values()

    def copy(self) -> dict[str, Any]:
        self._check()
        return dict(self)

    def __eq__(self, other: object) -> bool:
        self._check()
        return super().__eq__(other)

    def __ne__(self, other: object) -> bool:
        self._check()
        return super().__ne__(other)

    def __repr__(self) -> str:
        self._check()
        return super().__repr__()


def materialize(value: Any) -> Any:
    """Resolve returned containers while rejected-output errors can still be caught."""
    if isinstance(value, dict):
        return {key: materialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [materialize(item) for item in value]
    return value
