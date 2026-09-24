"""What every contract section shares: the contract version, value type names, ceilings, the effect-list and
type-name field types, and the base models."""
from __future__ import annotations

from typing import Annotated, Any, List

from pydantic import BaseModel, BeforeValidator, ConfigDict, WithJsonSchema, model_validator
from pydantic_core import PydanticCustomError

__all__ = ["CONTRACT_VERSION", "INPUT_TYPES", "PROP_TYPES", "PARAM_TYPES", "MAX_LIST_ITEMS", "OUTPUT_TYPES",
           "one_or_many", "Effects", "TYPE_SYNONYMS", "SPELLINGS", "TypeName", "MAX_ROUNDS", "MAX_STAGE_PASSES", "MAX_TURN_CALLS",
           "MAX_TURN_ACTIONS", "MAX_POPULATION", "MAX_CREATE", "MAX_SUBSTEPS"]

CONTRACT_VERSION = "1"

INPUT_TYPES = ("number", "int", "bool", "text", "enum", "list", "table", "map", "date", "any")
PROP_TYPES = ("number", "int", "bool", "text", "enum", "list", "map", "any", "asset")
PARAM_TYPES = ("number", "int", "bool", "text", "enum", "entity", "list", "file")
#: Most items a list argument may hold.
MAX_LIST_ITEMS = 1_000
OUTPUT_TYPES = ("number", "int", "bool", "text", "list", "map", "any")


def one_or_many(value: Any) -> Any:
    """One effect or condition written where a list goes: `"do": "$actor.coins += 1"` means `["$actor.coins += 1"]`."""
    return [value] if isinstance(value, (str, dict)) else value


#: A list of effects, or one effect (an assignment text or an operation object) on its own.
Effects = Annotated[List[Any], BeforeValidator(one_or_many),
                    WithJsonSchema({"anyOf": [{"type": "array", "items": {}}, {"type": "string"}, {"type": "object"}]})]

#: Common spellings of the type names, read as the names the contract uses.
TYPE_SYNONYMS = {"integer": "int", "string": "text", "boolean": "bool", "float": "number"}
#: How a type field's description names them.
SPELLINGS = " (`integer`, `float`, `string` and `boolean` are read as int, number, text and bool)"


def _type_name(value: Any) -> Any:
    return TYPE_SYNONYMS.get(value, value) if isinstance(value, str) else value


#: A type name; `integer`, `string`, `boolean` and `float` are read as int, text, bool and number.
TypeName = Annotated[str, BeforeValidator(_type_name)]

# Ceilings: generous for any real environment, low enough that a typo cannot make a run
# effectively infinite or exhaust memory.

#: Most rounds a run may last.
MAX_ROUNDS = 100_000
#: Most passes a stage may make through its agents in one round.
MAX_STAGE_PASSES = 10_000
#: Most tool calls (including looks) one turn may allow.
MAX_TURN_CALLS = 1_000
#: Most actions one turn may allow.
MAX_TURN_ACTIONS = 1_000
#: Most entities one population group may generate.
MAX_POPULATION = 1_000_000
#: Most entities one ``create`` effect may make (checked where the count is a literal).
MAX_CREATE = 100_000
#: Most physics sub-steps per round.
MAX_SUBSTEPS = 10_000


def _ceiling(value: Any, limit: int, fix: str) -> Any:
    """Reject a literal whole number above ``limit``."""
    if isinstance(value, int) and not isinstance(value, bool) and value > limit:
        raise PydanticCustomError("ceiling", "is {value}, above the ceiling of {limit}",
                                  {"value": f"{value:,}", "limit": f"{limit:,}", "fix": fix})
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)


class _ExprShorthand(_Model):
    """Accept a bare expression text in place of the object (``"$x > 0"`` → ``{"expr": "$x > 0"}``)."""

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any) -> Any:
        return data if isinstance(data, dict) else {"expr": data}
