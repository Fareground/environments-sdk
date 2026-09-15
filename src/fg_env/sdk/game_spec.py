"""The contract's `game` section: seats, what each scores, and the utility class."""
from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GameSpec", "UTILITIES"]

UTILITIES = ("zero_sum", "constant_sum", "general_sum", "identical")


class GameSpec(BaseModel):
    """Who the players are and what each one scores, for `RunResult.returns`, `fg_env.game` and `fg_env.gym`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    players: Union[str, List[str], None] = Field(None, description="Agent type(s) whose entities are the seats (default: every agent).")
    seat: Optional[str] = Field(None, description="Seat order: an expression over $it, lowest first (default: the order entities are created).")
    returns: Optional[str] = Field(None, description="A seat's total return so far: an expression over $actor, read after every decision and at the end.")
    rewards: Optional[str] = Field(None, description="A seat's reward for its latest step: an expression over $actor (default: the change in `returns` since that seat's previous step).")
    utility: str = Field("general_sum", description="One of: " + ", ".join(UTILITIES) + " — checked on every finished run.")
    total: Optional[float] = Field(None, description="constant_sum: what every finished run's returns add up to.")
