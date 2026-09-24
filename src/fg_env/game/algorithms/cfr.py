"""Counterfactual regret minimization over an extracted tree: CFR (Zinkevich et al., 2007) and CFR+ (Tammelin, 2014).

Both update one seat at a time (alternating updates). CFR keeps cumulative regrets and averages strategies by
reach; CFR+ clips regrets at zero and weights iteration t's strategy by t. In two-player zero-sum games the
average policy converges to a Nash equilibrium; :func:`~.best_response.exploitability` measures how close it is.
"""
from __future__ import annotations

from ..game import Game
from .policy import TabularPolicy
from .tree import Chance, Decision, GameTree, Node, Terminal, extract_tree

__all__ = ["CFRSolver", "solve"]


class CFRSolver:
    """Tabular CFR (``plus=False``) or CFR+ (``plus=True``) on a game tree (or a game, whose tree is extracted)."""

    def __init__(self, source: GameTree | Game, *, plus: bool = False):
        self.tree = source if isinstance(source, GameTree) else extract_tree(source)
        self.plus = plus
        self.iteration = 0
        infosets = self.tree.infosets()
        self._actions: dict[str, list[str]] = {key: texts for key, (_, texts) in infosets.items()}
        self._regrets: dict[str, list[float]] = {key: [0.0] * len(texts) for key, texts in self._actions.items()}
        self._totals: dict[str, list[float]] = {key: [0.0] * len(texts) for key, texts in self._actions.items()}

    def iterate(self, iterations: int = 1) -> CFRSolver:
        """Run ``iterations`` more iterations (each updates every seat once, in seat order)."""
        if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 0:
            raise ValueError(f"iterations must be a whole number ≥ 0, got {iterations!r}")
        for _ in range(iterations):
            self.iteration += 1
            for player in range(self.tree.num_players):
                self._walk(self.tree.root, player, 1.0, 1.0)
        return self

    def current_policy(self) -> TabularPolicy:
        """The regret-matching policy of the latest iteration."""
        return TabularPolicy({key: dict(zip(texts, self._strategy(key))) for key, texts in self._actions.items()},
                             self.tree.game)

    def average_policy(self) -> TabularPolicy:
        """The average policy: the one that converges to equilibrium."""
        table = {}
        for key, texts in self._actions.items():
            totals = self._totals[key]
            whole = sum(totals)
            table[key] = dict(zip(texts, [value / whole for value in totals] if whole > 0 else
                                  [1.0 / len(texts)] * len(texts)))
        return TabularPolicy(table, self.tree.game)

    def _strategy(self, key: str) -> list[float]:
        regrets = self._regrets[key]
        positive = [value if value > 0 else 0.0 for value in regrets]
        whole = sum(positive)
        return [value / whole for value in positive] if whole > 0 else [1.0 / len(regrets)] * len(regrets)

    def _walk(self, node: Node, player: int, own_reach: float, other_reach: float) -> float:
        """The node's value to ``player``; updates ``player``'s regrets and average-strategy sums on the way."""
        if isinstance(node, Terminal):
            return node.returns[player]
        if isinstance(node, Chance):
            return sum(p * self._walk(child, player, own_reach, other_reach * p) for _, p, child in node.outcomes)
        assert isinstance(node, Decision)
        strategy = self._strategy(node.infoset)
        if node.player != player:
            return sum(s * self._walk(child, player, own_reach, other_reach * s)
                       for s, child in zip(strategy, node.children))
        values = [self._walk(child, player, own_reach * s, other_reach) for s, child in zip(strategy, node.children)]
        value = sum(s * v for s, v in zip(strategy, values))
        regrets, totals = self._regrets[node.infoset], self._totals[node.infoset]
        weight = float(self.iteration) if self.plus else 1.0
        for index, action_value in enumerate(values):
            regrets[index] += other_reach * (action_value - value)
            if self.plus and regrets[index] < 0:
                regrets[index] = 0.0
            totals[index] += weight * own_reach * strategy[index]
        return value


def solve(source: GameTree | Game, iterations: int, *, plus: bool = True,
          solver: CFRSolver | None = None) -> TabularPolicy:
    """The average policy after ``iterations`` of CFR+ (or CFR with ``plus=False``)."""
    return (solver or CFRSolver(source, plus=plus)).iterate(iterations).average_policy()
