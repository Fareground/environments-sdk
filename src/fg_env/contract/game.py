"""What a player type scores (``types.<t>.score``): each seat's value, the seat order, the utility class and bounds."""
from __future__ import annotations

from pydantic import Field

from .base import _Model

__all__ = ["ScoreSpec", "UTILITIES"]

UTILITIES = ("zero_sum", "constant_sum", "general_sum", "identical")


class ScoreSpec(_Model):
    """What each agent of this type (a seat) scores, for `RunResult.returns`, tournaments, `fg_env.rl.game` and
    `fg_env.rl.gym`. The seats are the entities of every type with a score, in creation order unless `seat` orders
    them; a reward is the change in a seat's value since its previous step."""

    value: str = Field(..., description="A seat's total score so far: an expression over $it (the seat) and $result, "
                                        "read after every decision and at the end.")
    seat: str | None = Field(None,
                             description="Seat order: an expression over $it, lowest first (default: the order "
                                         "entities are created). Every scoring type orders seats the same way.")
    utility: str = Field("general_sum",
                         description="One of: " + ", ".join(UTILITIES) + " — the same on every scoring type; "
                                     "zero_sum and identical are checked on every finished run.")
    min: float | None = Field(None, description="The lowest value a seat can finish with — checked on every finished "
                                                "run.")
    max: float | None = Field(None, description="The highest value a seat can finish with — checked on every finished "
                                                "run.")
