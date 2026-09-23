"""Algorithms as participants: ``"mcts:1000"``, ``"ismcts:500"``, ``"minimax"``, ``"minimax:4"``,
``"cfr:policy.json"`` and ``"cfr:2000"`` play any seat of a run, in tournaments and experiments alike.

* ``mcts[:simulations]`` and ``minimax[:depth]`` search a private copy of the run paused in the turn, with every
  seat under their control and chance explicit from there on. They see the whole state, so they refuse games
  with hidden information.
* ``ismcts[:simulations]`` rebuilds the seat's view of the game from the run's log (every chance outcome and
  call, checked against what the seat was told) and searches determinizations of it, so it never sees hidden
  facts. It plays one seat at a time; simultaneous stages need ``cfr``.
* ``cfr:<policy.json>`` plays a saved :class:`TabularPolicy`; ``cfr:<iterations>`` solves the game with CFR+
  first (once per contract). The policy is looked up by the seat's information state, computed from the live run.

Each is seeded from the run, so a run replays exactly.
"""
from __future__ import annotations

import hashlib
import os
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from ...api import load
from ...errors import RunError
from ...session import END_TURN, Wake
from ...snapshot import contract_hash, encode
from ..game import Game
from ..observe import digest, information_state
from ..space import COMBINATION_LIMIT, Action, legal_calls
from ..state import GameState
from .cfr import CFRSolver
from .mcts import ISMCTSBot, MCTSBot
from .minimax import MinimaxBot, check_perfect_information
from .policy import TabularPolicy

if TYPE_CHECKING:
    from ...contract import Contract

__all__ = ["ALGORITHMS", "algorithm_participant"]

ALGORITHMS = ("mcts", "ismcts", "minimax", "cfr")


def algorithm_participant(value: str, contract: "Contract", seed: int) -> Optional[Callable[[Wake], Any]]:
    """The participant a string like ``"mcts:1000"`` names, or None when it names no algorithm."""
    name, _, argument = value.partition(":")
    if name not in ALGORITHMS:
        return None
    if name == "cfr":
        if not argument:
            raise ValueError("cfr needs a policy file or a number of iterations: 'cfr:policy.json' or 'cfr:2000'")
        return PolicyPlayer(argument, seed)
    number = _whole(argument, name) if argument else None
    if name == "mcts":
        return SearchPlayer("mcts", lambda s: MCTSBot(number or 1000, seed=s), seed)
    if name == "ismcts":
        return SearchPlayer("ismcts", lambda s: ISMCTSBot(number or 1000, seed=s), seed)
    return SearchPlayer("minimax", lambda s: MinimaxBot(number, seed=s), seed)


def _whole(text: str, name: str) -> int:
    try:
        number = int(text)
    except ValueError:
        number = 0
    if number < 1:
        raise ValueError(f"'{name}:{text}': the number after '{name}:' must be a whole number ≥ 1")
    return number


def _turn_seed(seed: int, wake: Wake) -> int:
    turn = wake._turn
    raw = repr((seed, turn.actor.id, turn.round, turn.number, turn.calls_left)).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


class _Games:
    """One :class:`Game` per contract, inputs, arm and seed a participant plays in."""

    def __init__(self) -> None:
        self._games: Dict[Tuple[str, str, Optional[str], int], Game] = {}

    def of(self, wake: Wake) -> Game:
        env = wake._turn.env
        key = (contract_hash(env.contract), repr(encode(env.inputs)), env.arm, env.seed)
        if key not in self._games:
            root = load(env.origin.unarmed, inputs=env.inputs, seed=env.seed, arm=env.arm)
            self._games[key] = Game(root, players=None, others=None, chance="explicit", turn_based=False, dry_run=True,
                                    limit=COMBINATION_LIMIT)
        return self._games[key]


class SearchPlayer:
    """Takes each decision of its turns with a search algorithm (see the module docs)."""

    def __init__(self, kind: str, make_bot: Callable[[int], Any], seed: int):
        self.kind = kind
        self.make_bot = make_bot
        self.seed = seed
        self._games = _Games()

    def __call__(self, wake: Wake) -> None:
        game = self._games.of(wake)
        if wake._turn.staged:
            raise RunError(f"{self.kind} decides one seat at a time and cannot play the simultaneous stage "
                           f"'{wake._turn.stage.name}'; use a cfr policy there", f"participants.{self.kind}")
        while not wake.done:
            state = self._state(wake, game)
            try:
                action = self.make_bot(_turn_seed(self.seed, wake)).step(state)
            finally:
                state.close()
            if action.tool == END_TURN:
                wake.end()
                return
            result = wake.call(action.tool, dict(action.args))
            if not result.ok:
                raise RunError(f"{self.kind} chose {action.text}, which the run refused: {result.text}",
                               f"participants.{self.kind}")

    def _state(self, wake: Wake, game: Game) -> GameState:
        if self.kind == "ismcts":
            return mirror(wake, game)
        from ...branch import clone_turn

        branch = clone_turn(wake._turn, controlled=set(game.players), explicit=True, same_luck=True)
        state = GameState(game, branch, [], path=os.urandom(20))  # a fresh history key: no position is shared
        check_perfect_information(state, self.kind)
        return state


def mirror(wake: Wake, game: Game) -> GameState:
    """The game state of ``wake``'s turn rebuilt from the run's log: chance outcomes and successful calls replayed
    on a new initial state, checked against the seat's information state in the live run."""
    turn = wake._turn
    env, actor = turn.env, turn.actor
    if actor.id not in game.players:
        raise RunError(f"'{actor.id}' is not one of the game's seats", "participants.ismcts")
    seat = game.seat(actor.id)
    events = [(event.kind, event.actor, dict(event.data)) for event in env.world.log if event.kind in ("chance", "action")]
    target = information_state(env, actor, turn)
    state = game.new_initial_state()
    try:
        for kind, who, data in events:
            if kind == "chance":
                state.apply_action(int(data["index"]))
            elif data.get("success", True) and who in game.players:
                _reach_turn_of(state, game.seat(who))
                state.apply_action({"tool": data["action"], "args": data.get("params") or {}})
        _reach_turn_of(state, seat)
        if state.information_state_string(seat) != target:
            raise RunError("the run's log does not rebuild what this seat knows (its calls' arguments or its luck "
                           "differ from the game's); play this contract through fg_env.rl.game with IS-MCTS directly",
                           "participants.ismcts")
    except (ValueError, RunError) as exc:
        state.close()
        raise RunError(f"ismcts could not rebuild the game at {actor.id}'s turn: {exc}", "participants.ismcts") from None
    return state


def _reach_turn_of(state: GameState, seat: int) -> None:
    """End the turns of seats that ended them without a logged call, until ``seat`` decides."""
    while not state.is_terminal() and not state.is_chance_node() and state.current_player() != seat:
        if END_TURN not in {action.tool for action in state.legal_tool_calls()}:
            raise ValueError(f"seat {state.current_player()} is to act, not seat {seat}")
        state.apply_action(END_TURN)


class PolicyPlayer:
    """Plays a tabular policy by information state (see the module docs)."""

    def __init__(self, source: str, seed: int):
        self.source = source
        self.seed = seed
        self._iterations = int(source) if source.isdigit() else None
        self._policy = None if self._iterations is not None else TabularPolicy.load(source)
        self._solved: Dict[str, TabularPolicy] = {}
        self._games = _Games()

    def __call__(self, wake: Wake) -> None:
        turn = wake._turn
        env, actor = turn.env, turn.actor
        policy = self._policy_for(wake)
        rng = random.Random(_turn_seed(self.seed, wake))
        while not wake.done:
            key = digest(information_state(env, actor, turn))
            calls, _ = legal_calls(env, turn)
            if not calls:
                wake.end()
                return
            if key not in policy:
                raise RunError(f"the policy {self.source} has no entry for {actor.id}'s information state here: it was "
                               f"made for another version of this contract (policy game: {policy.game or 'unknown'})",
                               "participants.cfr")
            actions = [Action(None, tool, args) for tool, args in calls]
            probabilities = policy.probabilities(key, [action.text for action in actions])
            chosen = rng.choices(actions, probabilities)[0]
            if chosen.tool == END_TURN:
                wake.end()
                return
            result = wake.call(chosen.tool, dict(chosen.args))
            if not result.ok:
                raise RunError(f"the policy chose {chosen.text}, which the run refused: {result.text}", "participants.cfr")

    def _policy_for(self, wake: Wake) -> TabularPolicy:
        if self._policy is not None:
            return self._policy
        game = self._games.of(wake)
        if game.id not in self._solved:
            assert self._iterations is not None
            solving = game.as_turn_based() if game.info["dynamics"] != "sequential" else game
            self._solved[game.id] = CFRSolver(solving, plus=True).iterate(self._iterations).average_policy()
        return self._solved[game.id]
