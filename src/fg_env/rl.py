"""Agents in the loop: a contract as a game, a Gymnasium or PettingZoo environment, a tournament or an evaluation.

    from fg_env import rl

    g = rl.game("nim.json")                              # OpenSpiel-style: seats, numbered actions, chance nodes
    env = rl.gym("nim.json", "ann", others="random")     # one agent as a Gymnasium-style environment
    table = rl.tournament("poker.json", {"a": bot_a, "b": bot_b}, games=50)

* ``game`` / ``Game`` / ``GameState`` — any contract as a game for search, solving and learning code;
  ``conformance`` checks it, ``playthrough`` prints one game move by move. Transforms, benchmarks and verified
  algorithms live in :mod:`fg_env.game`.
* ``gym`` / ``GymEnv``, ``pettingzoo_aec`` / ``pettingzoo_parallel`` — reinforcement-learning adapters.
* ``tournament`` — pit participants against each other in the contract's seats, then rate and rank them.
* ``evaluate`` — how well a focal participant does among background agents, against a baseline on the same seeds.
"""
from .evaluate import EvaluationResult, evaluate
from .game import (
    ConformanceReport,
    Game,
    GameState,
    conformance,
    game,
    pettingzoo_aec,
    pettingzoo_parallel,
    playthrough,
)
from .game.gym import GymEnv, gym
from .tournament import TournamentResult, tournament

__all__ = ["game", "Game", "GameState", "conformance", "ConformanceReport", "playthrough", "gym", "GymEnv",
           "pettingzoo_aec", "pettingzoo_parallel", "tournament", "TournamentResult", "evaluate", "EvaluationResult"]
