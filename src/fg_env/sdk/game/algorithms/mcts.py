"""Monte Carlo tree search: UCT (Kocsis & Szepesvári, 2006) with random-rollout evaluation, and single-observer
information-set MCTS (Cowling, Powley & Whitehouse, 2012) for games with hidden information.

MCTS searches the true state, so it sees everything: use it where nothing is hidden. IS-MCTS searches from the
seat's information state instead: every simulation starts from a determinization — a state drawn so that the
seat's information state is exactly what it is now — and statistics are shared by call across determinizations.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from ..space import Action
from ..state import GameState
from ..steps import Step, apply_step

__all__ = ["RandomRolloutEvaluator", "MCTSBot", "ISMCTSBot", "determinize", "DETERMINIZATION_TRIES"]

#: Most attempts to draw a state consistent with a seat's information state before IS-MCTS gives up.
DETERMINIZATION_TRIES = 500


class RandomRolloutEvaluator:
    """Scores a state by the mean returns of ``rollouts`` uniformly random playouts to the end."""

    def __init__(self, rollouts: int = 1):
        if isinstance(rollouts, bool) or not isinstance(rollouts, int) or rollouts < 1:
            raise ValueError(f"rollouts must be a whole number ≥ 1, got {rollouts!r}")
        self.rollouts = rollouts

    def evaluate(self, state: GameState, rng: random.Random) -> List[float]:
        """Mean returns over the rollouts (the state itself is left unchanged)."""
        totals = [0.0] * state.game.num_players()
        for _ in range(self.rollouts):
            copy = state.clone()
            try:
                totals = [a + b for a, b in zip(totals, play_out(copy, rng))]
            finally:
                copy.close()
        return [total / self.rollouts for total in totals]


def play_out(state: GameState, rng: random.Random) -> List[float]:
    """Play ``state`` to the end with uniformly random legal calls and chance by its probabilities; its returns."""
    while not state.is_terminal():
        if state.is_chance_node():
            outcomes, probabilities = zip(*state.chance_outcomes())
            state.apply_action(rng.choices(outcomes, probabilities)[0])
        elif state.is_simultaneous_node():
            state.apply_actions({seat: _sampled(state, rng, seat) for seat in state.acting_players()})
        else:
            state.apply_action(_sampled(state, rng, state.current_player()))
    return state.returns()


def _sampled(state: GameState, rng: random.Random, seat: int) -> Action:
    action = state.sample_legal_action(rng, seat)
    if action is None:
        raise ValueError(f"seat {seat} must decide but has no listed legal call ({state.unlisted_actions(seat)})")
    return action


class _Node:
    __slots__ = ("visits", "totals", "children", "untried", "available")

    def __init__(self, players: int):
        self.visits = 0
        self.totals = [0.0] * players
        self.children: Dict[object, Tuple[Optional[Action], "_Node"]] = {}
        self.untried: Optional[List[Action]] = None
        self.available: Dict[object, int] = {}

    def child(self, key: object, action: Optional[Action], players: int) -> "_Node":
        found = self.children.get(key)
        if found is None:
            found = (action, _Node(players))
            self.children[key] = found
        return found[1]

    def mean(self, player: int) -> float:
        return self.totals[player] / self.visits if self.visits else 0.0


def _key(action: Action) -> object:
    return action.id if action.id is not None else action.text


def _backup(path: List[_Node], values: List[float]) -> None:
    for node in path:
        node.visits += 1
        node.totals = [total + value for total, value in zip(node.totals, values)]


def _choice(root: _Node, player: int) -> Action:
    visited = [(action, node) for action, node in root.children.values() if action is not None and node.visits]
    if not visited:
        raise ValueError("the search made no simulations")
    action, _ = max(visited, key=lambda item: (item[1].visits, item[1].mean(player)))
    assert action is not None
    return action


class MCTSBot:
    """UCT search with ``simulations`` simulations per decision. ``uct_c`` weighs exploration against the mean
    return (scale it with the game's returns); ``evaluator`` scores new leaves (default: one random rollout)."""

    def __init__(self, simulations: int = 1000, *, uct_c: float = 2.0, evaluator: Optional[RandomRolloutEvaluator] = None,
                 seed: int = 0):
        if isinstance(simulations, bool) or not isinstance(simulations, int) or simulations < 1:
            raise ValueError(f"simulations must be a whole number ≥ 1, got {simulations!r}")
        self.simulations = simulations
        self.uct_c = uct_c
        self.evaluator = evaluator
        self.rng = random.Random(seed)

    def step(self, state: GameState) -> Action:
        """The most-visited call for the seat to move."""
        if state.is_terminal() or state.is_chance_node() or state.is_simultaneous_node():
            raise ValueError("MCTS decides for one seat: the state is terminal, a chance node or a simultaneous node")
        player = state.current_player()
        return _choice(self.search(state), player)

    def search(self, state: GameState) -> "_Node":
        players = state.game.num_players()
        root = _Node(players)
        for _ in range(self.simulations):
            working = state.clone()
            try:
                path, values = self._simulate(working, root, players)
            finally:
                working.close()
            _backup(path, values)
        return root

    def _simulate(self, working: GameState, root: _Node, players: int) -> Tuple[List[_Node], List[float]]:
        node, path = root, [root]
        while True:
            if working.is_terminal():
                return path, working.returns()
            if working.is_chance_node():
                outcomes, probabilities = zip(*working.chance_outcomes())
                outcome = self.rng.choices(outcomes, probabilities)[0]
                working.apply_action(outcome)
                node = node.child(("chance", outcome), None, players)
                path.append(node)
                continue
            if working.is_simultaneous_node():
                raise ValueError("MCTS decides one seat at a time: search game.as_turn_based() for simultaneous stages")
            player = working.current_player()
            if node.untried is None:
                node.untried = list(working.legal_tool_calls(player))
                self.rng.shuffle(node.untried)
            if node.untried:
                action = node.untried.pop()
                working.apply_action(action)
                path.append(node.child(_key(action), action, players))
                return path, self._leaf(working)
            if not node.children:
                raise ValueError(f"seat {player} must decide but has no listed legal call")
            parent_log = math.log(node.visits) if node.visits else 0.0
            action, node = max(node.children.values(), key=lambda item: item[1].mean(player)
                               + self.uct_c * math.sqrt(parent_log / item[1].visits))  # type: ignore[assignment]
            assert action is not None
            working.apply_action(action)
            path.append(node)

    def _leaf(self, working: GameState) -> List[float]:
        if self.evaluator is not None:
            return self.evaluator.evaluate(working, self.rng)
        return play_out(working, self.rng)


def determinize(state: GameState, seat: int, rng: random.Random, tries: int = DETERMINIZATION_TRIES) -> GameState:
    """A state drawn from the ones ``seat`` cannot tell ``state`` apart from: its decisions are replayed from the
    start with chance outcomes and other seats' calls drawn again, each only among those consistent with what the
    seat was told (chance by its probabilities, calls uniformly), and the draw is kept when the seat's whole
    information state matches. Uses only the seat's information state and its own calls, never hidden facts."""
    target = state.information_state_string(seat)
    target_recall = _recall(target)
    steps = _own_view_of_history(state, seat)
    for _ in range(tries):
        drawn = _draw(state, seat, steps, target_recall, rng)
        if drawn is not None:
            if drawn.information_state_string(seat) == target:
                return drawn
            drawn.close()
    raise ValueError(f"no state consistent with seat {seat}'s information state was found in {tries} draws")


def _own_view_of_history(state: GameState, seat: int) -> List[Optional[Step]]:
    """The decisions so far with everything but the seat's own calls blanked: None to draw again."""
    out: List[Optional[Step]] = []
    for entry in state.history():
        if "player" in entry and entry["player"] == seat:
            out.append({"seat": seat, "tool": entry["tool"], "args": entry["args"]})
        else:
            out.append(None)
    return out


def _recall(information_state: str) -> List[str]:
    head, _, _ = information_state.rpartition("\n\nNow:")
    return head.splitlines()


def _draw(state: GameState, seat: int, steps: List[Optional[Step]], target: List[str],
          rng: random.Random) -> Optional[GameState]:
    drawn = state.game.new_initial_state()
    try:
        for step in steps:
            if step is not None:
                apply_step(drawn, step)
                if not _consistent(drawn, seat, target):
                    raise _Rejected
                continue
            if drawn.is_chance_node():
                options = [(outcome, p) for outcome, p in drawn.chance_outcomes()]
                keep = [(outcome, p) for outcome, p in options if _consistent_after(drawn, {"chance": outcome}, seat, target)]
                if not keep:
                    raise _Rejected
                outcome = rng.choices([o for o, _ in keep], [p for _, p in keep])[0]
                drawn.apply_action(outcome)
            else:
                player = drawn.current_player()
                calls = [{"seat": player, "tool": a.tool, "args": dict(a.args)} for a in drawn.legal_tool_calls(player)]
                keep_calls = [call for call in calls if _consistent_after(drawn, call, seat, target)]
                if not keep_calls:
                    raise _Rejected
                apply_step(drawn, rng.choice(keep_calls))
        return drawn
    except (_Rejected, ValueError):
        drawn.close()
        return None


class _Rejected(Exception):
    pass


def _consistent(state: GameState, seat: int, target: List[str]) -> bool:
    recall = _recall(state.information_state_string(seat))
    return recall == target[:len(recall)]


def _consistent_after(state: GameState, step: Step, seat: int, target: List[str]) -> bool:
    child = state.clone()
    try:
        apply_step(child, step)
        return _consistent(child, seat, target)
    except ValueError:
        return False
    finally:
        child.close()


class ISMCTSBot:
    """Single-observer information-set MCTS for the seat to move: ``simulations`` determinized simulations,
    statistics shared by call, selection by UCB over the calls available in each determinization."""

    def __init__(self, simulations: int = 1000, *, uct_c: float = 2.0, seed: int = 0,
                 tries: int = DETERMINIZATION_TRIES):
        if isinstance(simulations, bool) or not isinstance(simulations, int) or simulations < 1:
            raise ValueError(f"simulations must be a whole number ≥ 1, got {simulations!r}")
        self.simulations = simulations
        self.uct_c = uct_c
        self.tries = tries
        self.rng = random.Random(seed)

    def step(self, state: GameState) -> Action:
        if state.is_terminal() or state.is_chance_node() or state.is_simultaneous_node():
            raise ValueError("IS-MCTS decides for one seat: the state is terminal, a chance node or a simultaneous node")
        seat = state.current_player()
        players = state.game.num_players()
        root = _Node(players)
        for _ in range(self.simulations):
            working = determinize(state, seat, self.rng, self.tries)
            try:
                path, values = self._simulate(working, root, players)
            finally:
                working.close()
            _backup(path, values)
        return _choice(root, seat)

    def _simulate(self, working: GameState, root: _Node, players: int) -> Tuple[List[_Node], List[float]]:
        node, path = root, [root]
        while True:
            if working.is_terminal():
                return path, working.returns()
            if working.is_chance_node():
                outcomes, probabilities = zip(*working.chance_outcomes())
                outcome = self.rng.choices(outcomes, probabilities)[0]
                working.apply_action(outcome)
                node = node.child(("chance", outcome), None, players)
                path.append(node)
                continue
            if working.is_simultaneous_node():
                raise ValueError("IS-MCTS decides one seat at a time: search game.as_turn_based() for simultaneous stages")
            player = working.current_player()
            legal = working.legal_tool_calls(player)
            if not legal:
                raise ValueError(f"seat {player} must decide but has no listed legal call")
            for action in legal:
                node.available[_key(action)] = node.available.get(_key(action), 0) + 1
            untried = [action for action in legal if _key(action) not in node.children]
            if untried:
                action = self.rng.choice(untried)
                working.apply_action(action)
                path.append(node.child(_key(action), action, players))
                return path, play_out(working, self.rng)
            action = max(legal, key=lambda a: node.children[_key(a)][1].mean(player) + self.uct_c * math.sqrt(
                math.log(node.available[_key(a)]) / node.children[_key(a)][1].visits))
            working.apply_action(action)
            node = node.children[_key(action)][1]
            path.append(node)
