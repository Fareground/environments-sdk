"""Tournaments: pit participants against each other in a contract's seats, then rate and rank them.

* :func:`tournament` — round robin, all-play-all or Swiss, with seat rotation and duplicate seeds.
* :class:`TournamentResult` — standings (maximum-likelihood Elo with intervals, Glicko-2, win/draw/loss,
  score means), the Nash average, α-Rank and a Schulze vote, head-to-head records, every entrant's
  score in every seat, cost per entrant, every game, and a ``summary()``.
"""
from .evaluation import alpha_rank, margins, nash_average, schulze
from .play import tournament
from .ratings import EloRating, Glicko, elo_mle, glicko2_period
from .result import TournamentResult

__all__ = ["tournament", "TournamentResult", "EloRating", "Glicko", "elo_mle", "glicko2_period", "margins",
           "nash_average", "alpha_rank", "schulze"]
