"""Throwaway mechanism families for tests of the extension spine."""
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict

from fg_env.registry import FAMILIES, OPS, FamilySpec, family


class Nothing(BaseModel):
    """A mode that takes no config."""

    model_config = ConfigDict(extra="forbid")


@contextmanager
def scratch_family(name: str, doc: str = "A family registered by a test.") -> Iterator[FamilySpec]:
    """Register a family for the duration of a test; its modes, actions and effect op are gone afterwards."""
    spec = family(name, doc)
    try:
        yield spec
    finally:
        FAMILIES.pop(name, None)
        OPS.pop(name, None)
