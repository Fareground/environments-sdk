"""Throwaway mechanism families for tests of the extension spine."""
from contextlib import contextmanager
from typing import Iterator

from pydantic import BaseModel, ConfigDict

from fg_env.sdk.registry import FAMILIES, OPS, RENAMED_KINDS, RENAMED_OPS, FamilySpec, family


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
        for table in (RENAMED_KINDS, RENAMED_OPS):
            for key in [key for key, (owner, _) in table.items() if owner == name]:
                table.pop(key)
