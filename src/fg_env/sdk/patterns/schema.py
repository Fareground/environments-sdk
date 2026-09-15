"""The JSON schema of the ``patterns`` section: one strict object per kind, chosen by ``kind``.

Kind models and the models they share (fit, calendar effects …) are definitions of the contract schema, so each is
written once; the section refers to them."""
from __future__ import annotations

from typing import Any, Dict

from pydantic.json_schema import models_json_schema

from . import catalogue  # noqa: F401 — registers every kind
from .base import KINDS

__all__ = ["patterns_field_schema", "patterns_definitions"]

_REF = "#/$defs/{model}"


def _named() -> Dict[str, Any]:
    return {name: KINDS[name].model for name in sorted(KINDS)}


def patterns_field_schema() -> Dict[str, Any]:
    """The section's schema: {name: one of the kind definitions}."""
    return {"type": "object", "additionalProperties": {"oneOf": [{"$ref": _REF.format(model=model.__name__)}
                                                                  for model in _named().values()]},
            "description": "Named patterns of the world, read as $pattern.<name>; see the guide's patterns part."}


def patterns_definitions() -> Dict[str, Any]:
    """Definitions for every kind model and the models they use, to merge into the contract schema's ``$defs``."""
    _, schema = models_json_schema([(model, "validation") for model in _named().values()], by_alias=True,
                                   ref_template=_REF)
    return dict(schema.get("$defs", {}))
