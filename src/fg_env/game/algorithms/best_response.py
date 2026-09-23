"""Exact values of tabular policies: what every seat expects, the best response to a policy, NashConv and
exploitability (OpenSpiel's definitions).

The best response of seat p picks, in each of p's information states, the call with the highest value summed
over the states it cannot tell apart, each weighted by the chance and other seats' probability of reaching it.
``nash_conv`` is the sum over seats of (best-response value − policy value); ``exploitability`` divides it by the
number of seats. Both are 0 exactly at a Nash equilibrium.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Union

from ..game import Game
from .policy import TabularPolicy
from .tree import Chance, Decision, GameTree, Node, Terminal, extract_tree

__all__ = ["policy_values", "best_response_value", "best_response", "nash_conv", "exploitability"]


def _tree(source: Union[GameTree, Game]) -> GameTree:
    return source if isinstance(source, GameTree) else extract_tree(source)


def policy_values(source: Union[GameTree, Game], policy: TabularPolicy) -> List[float]:
    """Every seat's expected return when all seats play ``policy``."""
    tree = _tree(source)

    def value(node: Node) -> List[float]:
        if isinstance(node, Terminal):
            return list(node.returns)
        if isinstance(node, Chance):
            parts = [(p, value(child)) for _, p, child in node.outcomes]
        else:
            assert isinstance(node, Decision)
            probabilities = policy.probabilities(node.infoset, [action.text for action in node.actions])
            parts = [(p, value(child)) for p, child in zip(probabilities, node.children) if p > 0]
        return [sum(p * values[seat] for p, values in parts) for seat in range(tree.num_players)]

    return value(tree.root)


class _BestResponder:
    def __init__(self, tree: GameTree, policy: TabularPolicy, player: int):
        self.policy = policy
        self.player = player
        self.members: Dict[str, List[Tuple[Decision, float]]] = {}
        self.best: Dict[str, int] = {}
        self.values: Dict[int, float] = {}
        self._collect(tree.root, 1.0)

    def _collect(self, node: Node, reach: float) -> None:
        """Group the player's decision nodes by information state, with the reach of chance and the other seats."""
        stack: List[Tuple[Node, float]] = [(node, reach)]
        while stack:
            current, weight = stack.pop()
            if isinstance(current, Chance):
                stack.extend((child, weight * p) for _, p, child in current.outcomes)
            elif isinstance(current, Decision):
                if current.player == self.player:
                    self.members.setdefault(current.infoset, []).append((current, weight))
                    stack.extend((child, weight) for child in current.children)
                else:
                    probabilities = self._probabilities(current)
                    stack.extend((child, weight * p) for p, child in zip(probabilities, current.children))

    def _probabilities(self, node: Decision) -> List[float]:
        return self.policy.probabilities(node.infoset, [action.text for action in node.actions])

    def value(self, node: Node) -> float:
        known = self.values.get(id(node))
        if known is not None:
            return known
        if isinstance(node, Terminal):
            result = node.returns[self.player]
        elif isinstance(node, Chance):
            result = sum(p * self.value(child) for _, p, child in node.outcomes)
        else:
            assert isinstance(node, Decision)
            if node.player == self.player:
                result = self.value(node.children[self.action(node.infoset)])
            else:
                result = sum(p * self.value(child) for p, child in zip(self._probabilities(node), node.children) if p > 0)
        self.values[id(node)] = result
        return result

    def action(self, infoset: str) -> int:
        if infoset not in self.best:
            members = self.members[infoset]
            count = len(members[0][0].children)
            scores = [sum(weight * self.value(node.children[index]) for node, weight in members) for index in range(count)]
            self.best[infoset] = max(range(count), key=lambda index: scores[index])
        return self.best[infoset]


def best_response(source: Union[GameTree, Game], policy: TabularPolicy, player: int) -> TabularPolicy:
    """Seat ``player``'s deterministic best response to everyone else playing ``policy``."""
    tree = _tree(source)
    responder = _BestResponder(tree, policy, player)
    table = {}
    for infoset, members in responder.members.items():
        texts = [action.text for action in members[0][0].actions]
        chosen = responder.action(infoset)
        table[infoset] = {text: (1.0 if index == chosen else 0.0) for index, text in enumerate(texts)}
    return TabularPolicy(table, tree.game)


def best_response_value(source: Union[GameTree, Game], policy: TabularPolicy, player: int) -> float:
    """What seat ``player`` gets by best responding while every other seat plays ``policy``."""
    tree = _tree(source)
    return _BestResponder(tree, policy, player).value(tree.root)


def nash_conv(source: Union[GameTree, Game], policy: TabularPolicy) -> float:
    """Σ over seats of (best-response value − the policy's value): 0 at a Nash equilibrium."""
    tree = _tree(source)
    values = policy_values(tree, policy)
    return sum(best_response_value(tree, policy, seat) - values[seat] for seat in range(tree.num_players))


def exploitability(source: Union[GameTree, Game], policy: TabularPolicy) -> float:
    """NashConv divided by the number of seats (in two-player zero-sum games: the average gain from best responding)."""
    tree = _tree(source)
    return nash_conv(tree, policy) / tree.num_players
