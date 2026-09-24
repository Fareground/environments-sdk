"""A game's whole tree, extracted once, so tabular algorithms (CFR, best response, exact values) can sweep it
many times without the engine.

Every decision node keeps the acting seat, its information-state key and its legal calls; chance nodes keep
each outcome's label and probability; terminal nodes keep the returns. A simultaneous stage has no single
acting seat: extract ``game.as_turn_based()`` instead, whose later seats cannot see earlier sealed choices.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..game import Game
from ..space import Action
from ..state import CHANCE, GameState

__all__ = ["MAX_TREE_NODES", "Terminal", "Chance", "Decision", "Node", "GameTree", "extract_tree"]

#: Most nodes extracted before extraction stops (a game this large wants sampling, not a table).
MAX_TREE_NODES = 250_000


class Terminal:
    """The end of the game: every seat's return."""

    __slots__ = ("returns",)

    def __init__(self, returns: tuple[float, ...]):
        self.returns = returns


class Chance:
    """A chance node: ``(label, probability, child)`` per possible outcome."""

    __slots__ = ("outcomes",)

    def __init__(self, outcomes: list[tuple[str, float, Node]]):
        self.outcomes = outcomes


class Decision:
    """A seat's decision: its information-state key, its legal calls and the child each one leads to."""

    __slots__ = ("player", "infoset", "actions", "children")

    def __init__(self, player: int, infoset: str, actions: list[Action], children: list[Node]):
        self.player = player
        self.infoset = infoset
        self.actions = actions
        self.children = children


Node = Terminal | Chance | Decision


@dataclass(frozen=True)
class GameTree:
    """An extracted game tree: the root, the number of seats, the game's id and how many nodes it has."""

    root: Node
    num_players: int
    game: str
    nodes: int

    def decisions(self) -> Iterator[Decision]:
        """Every decision node, depth first."""
        stack: list[Node] = [self.root]
        while stack:
            node = stack.pop()
            if isinstance(node, Decision):
                yield node
                stack.extend(reversed(node.children))
            elif isinstance(node, Chance):
                stack.extend(child for _, _, child in reversed(node.outcomes))

    def infosets(self) -> dict[str, tuple[int, list[str]]]:
        """Every information state: the seat that acts in it and its calls (as text). Raises ValueError when one
        information state offers different calls in different states, which search over information states cannot
        represent (the calls depend on something the seat cannot see)."""
        found: dict[str, tuple[int, list[str]]] = {}
        for node in self.decisions():
            texts = [action.text for action in node.actions]
            known = found.setdefault(node.infoset, (node.player, texts))
            if known != (node.player, texts):
                raise ValueError(f"information state {node.infoset[:12]}… offers {known[1]} to seat {known[0]} in one "
                                 f"state and {texts} to seat {node.player} in another: the legal calls depend on "
                                 "something the seat cannot see (run fg_env.rl.conformance to find the leak)")
        return found


def extract_tree(source: Game | GameState, *, max_nodes: int = MAX_TREE_NODES) -> GameTree:
    """The whole tree below a state (or a game's initial state). Needs declared returns and listed calls."""
    state = source.new_initial_state() if isinstance(source, Game) else source.clone()
    game = state.game
    if game.contract.game is None or game.contract.game.returns is None:
        state.close()
        raise ValueError("the game declares no returns, so its tree has no values: declare game.returns")
    counter = [0]
    root = _expand(state, counter, max_nodes)
    return GameTree(root, game.num_players(), game.id, counter[0])


def _expand(state: GameState, counter: list[int], limit: int) -> Node:
    """The subtree at ``state``, which this call owns and closes."""
    counter[0] += 1
    if counter[0] > limit:
        state.close()
        raise ValueError(f"the game tree has more than {limit:,} nodes; use sampling (mcts) instead, or a smaller game")
    handed = False
    try:
        if state.is_terminal():
            return Terminal(tuple(state.returns()))
        if state.is_chance_node():
            outcomes = state.chance_outcomes()
            chance: list[tuple[str, float, Node]] = []
            for position, (outcome, probability) in enumerate(outcomes):
                label = state.action_to_string(CHANCE, outcome).split(": ", 1)[-1]
                last = position == len(outcomes) - 1
                child = state if last else state.clone()
                handed = handed or last
                child.apply_action(outcome)
                chance.append((label, probability, _expand(child, counter, limit)))
            return Chance(chance)
        if state.is_simultaneous_node():
            raise ValueError("a simultaneous node has no single acting seat: extract game.as_turn_based() instead")
        player = state.current_player()
        unlisted = state.unlisted_actions(player)
        if unlisted:
            raise ValueError(f"seat {player} has calls that cannot be listed ({unlisted}), so the tree cannot hold "
                             "them")
        actions = list(state.legal_tool_calls(player))
        infoset = state.information_state(player)
        children: list[Node] = []
        for position, action in enumerate(actions):
            last = position == len(actions) - 1
            child = state if last else state.clone()
            handed = handed or last
            child.apply_action(action)
            children.append(_expand(child, counter, limit))
        return Decision(player, infoset, actions, children)
    finally:
        if not handed:
            state.close()
