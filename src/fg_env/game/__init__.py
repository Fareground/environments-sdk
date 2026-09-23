"""Games: any contract as an OpenSpiel-style game — seats, numbered actions, chance nodes, clones — with
conformance checks, playthroughs, transforms, PettingZoo adapters, benchmarks and verified algorithms
(:mod:`fg_env.game.algorithms`)."""
from .bench import GameBench, bench_game
from .conformance import ConformanceIssue, ConformanceReport, conformance
from .game import Game, game
from .pettingzoo import AECGame, ParallelGame, pettingzoo_aec, pettingzoo_parallel
from .playthrough import playthrough
from .space import COMBINATION_LIMIT, Action, ActionSpace
from .state import CHANCE, SIMULTANEOUS, TERMINAL, GameState
from .steps import Step, apply_step, random_step, replay_steps
from .transforms import misere, repeated, zero_sum_check, zerosum

__all__ = ["Game", "GameState", "Action", "ActionSpace", "game", "CHANCE", "SIMULTANEOUS", "TERMINAL",
           "COMBINATION_LIMIT", "conformance", "ConformanceReport", "ConformanceIssue", "playthrough", "bench_game",
           "GameBench", "Step", "apply_step", "random_step", "replay_steps", "repeated", "misere", "zerosum",
           "zero_sum_check", "pettingzoo_aec", "pettingzoo_parallel", "AECGame", "ParallelGame"]
