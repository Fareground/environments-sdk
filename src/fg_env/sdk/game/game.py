"""``fg_env.game``: any contract as a game for search, solving and learning code (OpenSpiel-style)."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ..api import ContractLike, load
from ..branch import Branch, copy_pilot
from ..errors import ContractError, Issue
from ..replay import Tape
from ..returns import seat_ids
from ..runtime import Env
from ..snapshot import contract_hash, decode, encode, run_identity
from .space import COMBINATION_LIMIT, ActionSpace
from .state import GameState

__all__ = ["Game", "game"]


class Game:
    """A contract as a game: seats, a numbered action space, and states to search from.

    Create with :func:`fg_env.game`. States pause at every decision of a seat (and at every chance
    node when chance is explicit); agents that are not seats are played by ``others``.
    """

    def __init__(self, root: Env, *, players: Optional[Sequence[str]], others: Any, chance: str, turn_based: bool,
                 dry_run: bool, limit: int):
        self._root = root
        self.contract = root.contract
        seats = list(players) if players is not None else seat_ids(self.contract, root.world)
        problems = [Issue("players", f"'{seat}' is not an agent of this game",
                          "name agent entities (see game.players or the contract's `game.players`)")
                    for seat in seats if seat not in root.world.entities
                    or not self.contract.is_agent(root.world.entities[seat].entity_type)]
        if not seats:
            problems.append(Issue("game.players", "there are no seats", "declare agent entities, or `game.players`"))
        if problems:
            raise ContractError(problems, title="the game has no valid seats")
        self.players: List[str] = seats
        self._seats: Dict[str, int] = {seat: index for index, seat in enumerate(seats)}
        self._others = others
        self.chance = chance
        self.turn_based = turn_based
        self.dry_run = dry_run
        self.limit = limit
        self.space = ActionSpace(root, limit)
        slug = re.sub(r"[^a-z0-9]+", "_", self.contract.name.lower()).strip("_") or "game"
        self.id = f"{slug}@{contract_hash(self.contract)[:8]}:{run_identity(root.seed, root.arm, encode(root.inputs))[:8]}"

    def num_players(self) -> int:
        return len(self.players)

    def num_distinct_actions(self) -> int:
        """The size of the action space (ids run from 0 to this minus 1)."""
        return self.space.size

    @property
    def utility(self) -> str:
        spec = self.contract.game
        return spec.utility if spec is not None else "general_sum"

    def seat(self, entity_id: str) -> int:
        """The seat index of an entity."""
        if entity_id not in self._seats:
            raise KeyError(f"'{entity_id}' is not a seat (seats: {', '.join(self.players)})")
        return self._seats[entity_id]

    def new_initial_state(self) -> GameState:
        root = self._root
        pilot = copy_pilot(root, Tape(), 0, root.origin.base, controlled=set(self.players),
                           explicit=self.chance == "explicit", participants=self._others, checkpoints=True)
        pilot.start()
        return GameState(self, Branch(pilot), [])

    def deserialize_state(self, text: str) -> GameState:
        """The state :meth:`GameState.serialize` wrote, rebuilt by replaying its decisions."""
        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"not a serialized game state: {exc}") from None
        if not isinstance(data, Mapping) or data.get("fg_env_game_state") != 1 or not isinstance(data.get("history"), list):
            raise ValueError("not a serialized game state (from GameState.serialize())")
        if data.get("game") != self.id:
            raise ValueError(f"the state belongs to game {data.get('game')!r}, not {self.id!r}")
        state = self.new_initial_state()
        for entry in decode(data["history"]):
            if "chance" in entry:
                state.apply_action(entry["chance"])
            else:
                state._apply(entry["player"], {"tool": entry["tool"], "args": entry["args"]})
        return state

    def as_turn_based(self) -> "Game":
        """The same game with sealed simultaneous turns played one seat at a time (later seats cannot see
        earlier seats' sealed choices)."""
        return Game(self._root, players=self.players, others=self._others, chance=self.chance, turn_based=True,
                    dry_run=self.dry_run, limit=self.limit)

    def __repr__(self) -> str:
        return f"<Game {self.id}: {self.num_players()} seats, {self.num_distinct_actions()} actions>"


def game(source: ContractLike, *, inputs: Optional[Mapping[str, Any]] = None, seed: int = 0, arm: Optional[str] = None,
         players: Optional[Sequence[str]] = None, others: Any = None, chance: str = "explicit",
         simultaneous: str = "joint", dry_run: bool = True, max_combinations: int = COMBINATION_LIMIT,
         hosts: Any = None, data_dir: Union[str, "os.PathLike[str]", None] = None) -> Game:
    """A contract as a game for search, solving and learning code.

    * ``players`` — the seats (entity ids); default: the contract's ``game.players`` (else every agent), in seat order.
    * ``others`` — participants for agents that are not seats (as in ``Env.run``).
    * ``chance`` — ``"explicit"``: every `chance` effect is a chance node whose outcomes search code chooses;
      ``"sampled"``: outcomes are drawn from the seed. Other randomness is always fixed by ``seed``.
    * ``simultaneous`` — ``"joint"``: a simultaneous stage is one node (``apply_actions``); ``"turn_based"``:
      its sealed turns are decided one seat at a time.
    * ``dry_run`` — legal calls are also tried without effect, so a call whose effects would refuse it is not
      listed (the engine's own judgement at submit); ``False`` lists every call that validates.
    * ``max_combinations`` — most argument combinations listed per action before it counts as parametric.
    """
    if chance not in ("explicit", "sampled"):
        raise ValueError(f"chance must be 'explicit' or 'sampled', got {chance!r}")
    if simultaneous not in ("joint", "turn_based"):
        raise ValueError(f"simultaneous must be 'joint' or 'turn_based', got {simultaneous!r}")
    if isinstance(max_combinations, bool) or not isinstance(max_combinations, int) or max_combinations < 1:
        raise ValueError(f"max_combinations must be a whole number ≥ 1, got {max_combinations!r}")
    root = load(source, inputs=inputs, seed=seed, arm=arm, data_dir=data_dir, hosts=hosts)
    return Game(root, players=players, others=others, chance=chance, turn_based=simultaneous == "turn_based",
                dry_run=dry_run, limit=max_combinations)
