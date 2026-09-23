"""Verified search and solving over any game: minimax with alpha-beta (expectimax over chance), MCTS and
information-set MCTS with a random-rollout evaluator, tabular CFR and CFR+ with best response and
exploitability. Each is usable from Python on :class:`~fg_env.game.GameState` and as a participant
string: ``"mcts:1000"``, ``"ismcts:500"``, ``"minimax"``, ``"minimax:4"``, ``"cfr:policy.json"``, ``"cfr:2000"``.
"""
from .best_response import best_response, best_response_value, exploitability, nash_conv, policy_values
from .cfr import CFRSolver, solve
from .mcts import ISMCTSBot, MCTSBot, RandomRolloutEvaluator, determinize
from .minimax import MinimaxBot, minimax
from .policy import TabularPolicy
from .tree import Chance, Decision, GameTree, Terminal, extract_tree

__all__ = ["CFRSolver", "solve", "TabularPolicy", "GameTree", "Terminal", "Chance", "Decision", "extract_tree",
           "policy_values", "best_response", "best_response_value", "nash_conv", "exploitability", "minimax",
           "MinimaxBot", "MCTSBot", "ISMCTSBot", "RandomRolloutEvaluator", "determinize"]
