"""A game state: one moment of a run, seen the way search and learning code sees games.

Decisions go through the same tool calls, validation and effects as an LLM agent's, so a game
state and an agent's turn can never disagree about what is legal or what a move does.

Legal calls are listed once per position: a state remembers them, its clones inherit them, and the
game remembers them by the decisions that led there (the same decisions always reach the same
state). A random playout can skip listing altogether: :meth:`GameState.sample_legal_action` tries
calls in random order and dry-runs only until one is legal.

The run behind a state is stepped on the caller's own thread where the contract allows it, and a clone
copies it directly; otherwise it is piloted on a thread of its own (:mod:`.runs`). Both behave identically.
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..branch import Branch, outcome_index
from ..driving import Unpausable
from ..errors import ContractError, Issue, RunError
from ..returns import seat_rewards
from ..run_copy import NotCopyable
from ..session import END_TURN, ToolResult
from ..snapshot import encode
from ..turn import Turn
from .observe import digest, information_state, observation_struct, observation_text, state_key
from .runs import Run, ThreadedRun, replayed
from .space import Action, legal_calls, sample_call

if TYPE_CHECKING:
    from .game import Game

__all__ = ["GameState", "CHANCE", "SIMULTANEOUS", "TERMINAL"]

#: ``current_player()`` at a chance node, a simultaneous node, and the end.
CHANCE, SIMULTANEOUS, TERMINAL = -1, -2, -4

ActionLike = Union[int, str, Action, Mapping[str, Any], Tuple[str, Mapping[str, Any]]]
Legal = Tuple[List[Action], Dict[str, str]]


class GameState:
    """One state of a :class:`~fg_env.sdk.game.Game`. ``apply_action`` changes it; ``child`` and ``clone``
    give new independent states. Players are seat indices (``game.players[i]`` is the entity id)."""

    def __init__(self, game: "Game", run: Union[Branch, Run], history: List[Dict[str, Any]],
                 previous: Optional[List[float]] = None, legal: Optional[Dict[int, Legal]] = None,
                 path: bytes = b""):
        self.game = game
        self._run: Run = ThreadedRun(run, game._prefetch) if isinstance(run, Branch) else run
        self._history = history
        self._previous = previous
        self._legal: Dict[int, Legal] = dict(legal) if legal else {}
        #: A digest of the decisions so far: equal digests, equal states.
        self._path = path
        #: A call just found legal by sampling, applied without listing again.
        self._verified: Optional[Tuple[int, str, str]] = None

    # -- whose decision -----------------------------------------------------------------------------

    def is_terminal(self) -> bool:
        if self._run.pause is not None:
            return False
        status, error = self._run.read(lambda env: (env.status, env.error))
        if status == "failed":
            raise RunError(f"the game's run failed: {error}", "game")
        return True

    def is_chance_node(self) -> bool:
        pause = self._run.pause
        return pause is not None and pause.kind == "chance"

    def is_simultaneous_node(self) -> bool:
        turn = self._turn()
        return turn is not None and turn.staged and not self.game.turn_based

    def current_player(self) -> int:
        """The acting seat, or ``CHANCE``, ``SIMULTANEOUS`` (several seats choose sealed moves) or ``TERMINAL``."""
        if self.is_terminal():
            return TERMINAL
        if self.is_chance_node():
            return CHANCE
        if self.is_simultaneous_node():
            return SIMULTANEOUS
        turn = self._turn()
        assert turn is not None
        return self.game.seat(turn.actor.id)

    def acting_players(self) -> List[int]:
        """Seats that decide now: every seat still choosing at a simultaneous node, else the current one."""
        turn = self._turn()
        if turn is None:
            return []
        if not turn.staged or self.game.turn_based:
            return [self.game.seat(turn.actor.id)]
        actors = self._run.read(lambda env: [t.actor.id for t in env.origin.staged if not t.done])
        return [self.game.seat(actor) for actor in actors if actor in self.game.players]

    def chance_outcomes(self) -> List[Tuple[int, float]]:
        """``(outcome, probability)`` for every possible outcome of the chance node."""
        pause = self._run.pause
        if pause is None or pause.kind != "chance" or pause.node is None:
            return []
        return [(outcome.index, outcome.p) for outcome in pause.node.possible]

    # -- actions -------------------------------------------------------------------------------------

    def legal_actions(self, player: Optional[int] = None) -> List[int]:
        """Ids of the legal listed calls (ascending); at a chance node, the possible outcomes."""
        if player is None and self.is_chance_node():
            return [outcome for outcome, _ in self.chance_outcomes()]
        return [action.id for action in self.legal_tool_calls(player) if action.id is not None]

    def legal_actions_mask(self, player: Optional[int] = None) -> List[int]:
        legal = set(self.legal_actions(player))
        return [1 if index in legal else 0 for index in range(self.game.num_distinct_actions())]

    def legal_tool_calls(self, player: Optional[int] = None) -> List[Action]:
        """The legal calls whose arguments can be listed, as :class:`Action` (id, tool, args)."""
        return self._legal_for(player)[0]

    def unlisted_actions(self, player: Optional[int] = None) -> Dict[str, str]:
        """Legal actions whose calls cannot be listed (free text, lists, …), with the reason; apply them as
        ``{"tool": ..., "args": {...}}``."""
        return self._legal_for(player)[1]

    def sample_legal_action(self, rng: random.Random, player: Optional[int] = None) -> Optional[Action]:
        """A uniformly random legal listed call of the seat (None when it has none) — the same choice as
        ``rng.choice(state.legal_tool_calls(player))``, found without listing every legal call, so random
        playouts are cheap. Applying it next does not list the legal calls either."""
        seat = self._seat(player)
        if seat < 0:
            return None
        known = self._legal.get(seat) or self.game._remembered(self._path, seat)
        if known is not None:
            return rng.choice(known[0]) if known[0] else None
        actor_id, game = self.game.players[seat], self.game

        def work(env: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
            turn = self._seat_turn(env, actor_id)
            return sample_call(env, turn, rng, limit=game.limit, dry_run=game.dry_run) if turn is not None else None

        found = self._run.read(work)
        if found is None:
            return None
        action = self._with_id(*found)
        self._verified = (seat, action.tool, _args_key(action.args))
        return action

    def action_to_string(self, player: int, action: ActionLike) -> str:
        if player == CHANCE:
            pause = self._run.pause
            if pause is not None and pause.node is not None:
                index = outcome_index(pause.node, action if isinstance(action, (int, str)) else -1)
                return f"{pause.node.name}: {pause.node.outcomes[index].label}"
        tool, args = self._resolve(action)
        return Action(None, tool, args).text

    def apply_action(self, action: ActionLike) -> None:
        """Take one decision: a chance outcome, or the acting seat's tool call (id, :class:`Action`,
        ``{"tool", "args"}``, ``(tool, args)`` or a tool name). An illegal call raises and changes nothing."""
        if self.is_terminal():
            raise ValueError("the game is over")
        if self.is_chance_node():
            pause = self._run.pause
            assert pause is not None and pause.node is not None
            index = outcome_index(pause.node, action.id if isinstance(action, Action) and action.id is not None
                                  else action)  # type: ignore[arg-type]
            self._before()
            self._decide(lambda run: run.choose(index))
            self._record({"chance": index, "outcome": pause.node.outcomes[index].label})
            self._changed()
            return
        if self.is_simultaneous_node():
            raise ValueError("this is a simultaneous node: give every acting seat's move with apply_actions({seat: "
                             "action}), or play it one seat at a time with game.as_turn_based()")
        self._apply(self.current_player(), action)

    def apply_actions(self, actions: Union[Mapping[int, ActionLike], Sequence[ActionLike]]) -> None:
        """Every acting seat's sealed move at a simultaneous node (a mapping seat → action, or a list in
        ``acting_players()`` order). Each seat's turn ends after its move."""
        if not self.is_simultaneous_node():
            raise ValueError("apply_actions is for simultaneous nodes; use apply_action")
        acting = self.acting_players()
        joint = dict(actions) if isinstance(actions, Mapping) else dict(zip(acting, actions))
        missing = [seat for seat in acting if seat not in joint]
        if missing or len(joint) != len(acting):
            raise ValueError(f"give exactly one action for each acting seat {acting} (missing: {missing})")
        calls = {seat: self._checked(seat, joint[seat]) for seat in acting}
        self._before()
        for seat in acting:
            turn = self._turn()
            if turn is None or self.game.seat(turn.actor.id) != seat:
                raise RunError(f"seat {seat}'s sealed turn is not the one waiting (the stage changed who acts)", "game")
            tool, args = calls[seat]
            self._call(seat, tool, args)
            turn_after = self._turn()
            if tool != END_TURN and turn_after is turn:
                self._call(seat, END_TURN, {})
        self._changed()

    def child(self, action: ActionLike) -> "GameState":
        state = self.clone()
        state.apply_action(action)
        return state

    def clone(self) -> "GameState":
        try:
            run = self._run.clone()
        except NotCopyable:
            run = replayed(self.game, self._history)
        return GameState(self.game, run, list(self._history),
                         list(self._previous) if self._previous is not None else None, self._legal, self._path)

    # -- scores --------------------------------------------------------------------------------------

    def returns(self) -> List[float]:
        """Each seat's return so far (the contract's ``game.returns``)."""
        game = self.game
        if game.contract.game is None or game.contract.game.returns is None:
            raise ContractError([Issue("game.returns", "is not declared, so the game has no returns",
                                       'declare what each seat scores, e.g. "game": {"returns": "$actor.chips"}')])
        run = self._run
        values = run.read_prefetched() if run.prefetch is not None else run.read(game._returns_of)
        return [values[seat] for seat in game.players]

    def rewards(self) -> List[float]:
        """Each seat's reward for the last decision: the contract's ``game.rewards``, else the change in returns."""
        contract, players = self.game.contract, self.game.players
        declared = self._run.read(lambda env: seat_rewards(contract, env.world, players))
        if declared is not None:
            return [declared[seat] for seat in players]
        now = self.returns()
        if self._previous is None:
            return [0.0] * len(now)
        return [after - before for after, before in zip(now, self._previous)]

    # -- what seats know -----------------------------------------------------------------------------

    def observation_string(self, player: int) -> str:
        """Exactly the update the seat would read now."""
        return str(self.observation(player, "text"))

    def observation(self, player: int, form: str = "text") -> Any:
        """The seat's observation: ``"text"`` (its update) or ``"struct"`` (what it may see, as data)."""
        if form not in ("text", "struct"):
            raise ValueError(f"form must be 'text' or 'struct', got {form!r}")
        actor_id = self.game.players[player]
        turn = self._turn()
        actions = [{"id": a.id, "tool": a.tool, "args": dict(a.args)} for a in self.legal_tool_calls(player)] \
            if form == "struct" and player in self.acting_players() else None
        if form == "text":
            return self._run.read(lambda env: observation_text(env, env.world.entities[actor_id], turn))
        return self._run.read(lambda env: observation_struct(env, env.world.entities[actor_id], turn, actions))

    def information_state_string(self, player: int) -> str:
        """Perfect recall of what the seat was told and did, then what it sees now."""
        actor_id = self.game.players[player]
        turn = self._turn()
        return str(self._run.read(lambda env: information_state(env, env.world.entities[actor_id], turn)))

    def information_state(self, player: int) -> str:
        """A key for the seat's information state (equal keys: the seat cannot tell the states apart)."""
        return digest(self.information_state_string(player))

    def state_key(self) -> str:
        """A key for the world and the pending decision (equal keys: the same position)."""
        return str(self._run.read(lambda env: state_key(env, self._pending(env))))

    # -- history -------------------------------------------------------------------------------------

    def history(self) -> List[Dict[str, Any]]:
        """Every decision so far: ``{"player", "tool", "args"}`` or ``{"chance", "outcome"}``."""
        return [dict(entry) for entry in self._history]

    def move_number(self) -> int:
        return len(self._history)

    def serialize(self) -> str:
        return json.dumps({"fg_env_game_state": 1, "game": self.game.id, "history": encode(self._history)},
                          sort_keys=True)

    def result(self) -> Any:
        """The run's :class:`RunResult` so far."""
        return self._run.result()

    def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return self._run.entity(entity_id)

    def close(self) -> None:
        """Discard the state's run (a piloted run's thread stops); also done when the state is garbage collected."""
        self._run.close()

    def __str__(self) -> str:
        if self.is_terminal():
            return f"terminal after {self.move_number()} moves, returns {self._safe_returns()}"
        if self.is_chance_node():
            return f"chance node after {self.move_number()} moves"
        return f"seat(s) {self.acting_players()} to move after {self.move_number()} moves"

    # -- internals -----------------------------------------------------------------------------------

    def _safe_returns(self) -> Optional[List[float]]:
        contract = self.game.contract
        return self.returns() if contract.game is not None and contract.game.returns is not None else None

    def _turn(self) -> Optional[Turn]:
        pause = self._run.pause
        return pause.wake._turn if pause is not None and pause.kind == "turn" and pause.wake is not None else None

    def _seat(self, player: Optional[int]) -> int:
        if player is None:
            current = self.current_player()
            if current == SIMULTANEOUS:
                raise ValueError(f"a simultaneous node: name the seat (acting: {self.acting_players()})")
            return current
        if isinstance(player, bool) or not isinstance(player, int) or not 0 <= player < self.game.num_players():
            raise ValueError(f"player must be a seat index from 0 to {self.game.num_players() - 1}, got {player!r}")
        return player

    def _legal_for(self, player: Optional[int]) -> Legal:
        seat = self._seat(player)
        if seat < 0:
            return [], {}
        known = self._legal.get(seat)
        if known is not None:
            return known
        known = self.game._remembered(self._path, seat)
        if known is None:
            actor_id, game = self.game.players[seat], self.game

            def work(env: Any) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, str]]:
                turn = self._seat_turn(env, actor_id)
                return legal_calls(env, turn, limit=game.limit, dry_run=game.dry_run) if turn is not None else ([], {})

            calls, unlisted = self._run.read(work)
            actions = sorted((self._with_id(tool, args) for tool, args in calls),
                             key=lambda a: a.id if a.id is not None else -1)
            known = (actions, unlisted)
            self.game._remember(self._path, seat, known)
        self._legal[seat] = known
        return known

    def _with_id(self, tool: str, args: Dict[str, Any]) -> Action:
        action = self.game.space.action(tool, args)
        if action.id is None and tool not in self.game.space.parametric:
            raise RunError(f"{action.text} is legal now but has no id: its arguments were not among the values "
                           "listed when the game was created; give `values` as a list, and `min`/`max` that do "
                           "not depend on the state, so every value has an id", f"actions.{tool}")
        return action

    def _seat_turn(self, env: Any, actor_id: str) -> Optional[Turn]:
        turn = self._turn()
        if turn is not None and turn.actor.id == actor_id:
            return turn
        if turn is not None and turn.staged:
            return next((t for t in env.origin.staged if t.actor.id == actor_id and not t.done), None)
        return None

    def _resolve(self, action: ActionLike) -> Tuple[str, Dict[str, Any]]:
        if isinstance(action, Action):
            return action.tool, dict(action.args)
        if isinstance(action, bool):
            raise ValueError(f"an action is an id, an Action, a tool call or a tool name, got {action!r}")
        if isinstance(action, int):
            return self.game.space.decode(action)
        if isinstance(action, str):
            return action, {}
        if isinstance(action, Mapping):
            tool = action.get("tool", action.get("name"))
            args = action.get("args", action.get("input", {}))
            if isinstance(tool, str) and isinstance(args, Mapping):
                return tool, dict(args)
        if isinstance(action, tuple) and len(action) == 2 and isinstance(action[0], str) and isinstance(action[1], Mapping):
            return action[0], dict(action[1])
        raise ValueError(f"an action is an id, an Action, {{'tool': ..., 'args': {{...}}}}, (tool, args) or a tool "
                         f"name, got {action!r}")

    def _checked(self, seat: int, action: ActionLike) -> Tuple[str, Dict[str, Any]]:
        """The call, once it is known to be legal for ``seat``."""
        tool, args = self._resolve(action)
        if self._verified is not None and self._verified == (seat, tool, _args_key(args)):
            return tool, args
        listed, unlisted = self._legal_for(seat)
        action_id = self.game.space.encode(tool, args)
        match = next((a for a in listed if (action_id is not None and a.id == action_id)
                      or (a.tool == tool and dict(a.args) == args)), None)
        if match is not None:
            return match.tool, dict(match.args)  # the listed spelling, so histories compare equal
        if tool in unlisted:
            actor_id = self.game.players[seat]

            def check(env: Any) -> Optional[str]:
                from ..assets.intake import previewed

                turn = self._seat_turn(env, actor_id)
                if turn is None:
                    return "the seat is not acting"
                with previewed(turn, tool, args) as (checked, problem):  # files as the call will submit them
                    if problem is not None:
                        return problem
                    params, problem = env.actions.validate(turn.actor, tool, checked)
                    return problem or env.actions.dry_run(turn.actor, tool, params)

            problem = self._run.read(check)
            if problem is None:
                return tool, args
            raise ValueError(f"{Action(action_id, tool, args).text} is not legal now: {problem}")
        shown = ", ".join(a.text for a in listed[:12]) + (" …" if len(listed) > 12 else "")
        raise ValueError(f"{Action(action_id, tool, args).text} is not legal for seat {seat} now (legal: {shown or 'none'})")

    def _apply(self, seat: int, action: ActionLike) -> None:
        tool, args = self._checked(seat, action)
        self._before()
        self._call(seat, tool, args)
        self._changed()

    def _call(self, seat: int, tool: str, args: Dict[str, Any]) -> None:
        result = self._decide(lambda run: run.call(tool, args))
        if result is not None and not result.ok:
            raise RunError(f"the engine refused {Action(None, tool, args).text} after it was found legal: {result.text}",
                           f"actions.{tool}")
        self._record({"player": seat, "tool": tool, "args": args})

    def _decide(self, decision: Callable[[Run], Optional[ToolResult]]) -> Optional[ToolResult]:
        """``decision`` on the state's run; a stepped run that cannot take it goes on as a piloted one."""
        try:
            return decision(self._run)
        except (Unpausable, NotCopyable):
            self._run.close()
            self._run = replayed(self.game, self._history)
            return decision(self._run)

    def _record(self, entry: Dict[str, Any]) -> None:
        self._history.append(entry)
        step = json.dumps(encode(entry), sort_keys=True, default=str).encode()
        self._path = hashlib.blake2b(self._path + step, digest_size=20).digest()

    def _before(self) -> None:
        contract = self.game.contract
        if contract.game is not None and contract.game.returns is not None:
            self._previous = self.returns()

    def _changed(self) -> None:
        self._legal.clear()
        self._verified = None

    def _pending(self, env: Any) -> Dict[str, Any]:
        pause = self._run.pause
        if pause is None:
            return {"over": True}
        if pause.kind == "chance" and pause.node is not None:
            return {"chance": pause.node.name, "site": pause.node.site,
                    "outcomes": [[o.label, o.p] for o in pause.node.outcomes]}
        turn = self._turn()
        assert turn is not None
        sealed = {t.actor.id: encode(t.pending) for t in env.origin.staged}
        return {"actor": turn.actor.id, "stage": turn.stage.name, "calls_left": turn.calls_left,
                "actions_left": turn.actions_left, "pending": encode(turn.pending), "used": dict(turn.used),
                "sealed": sealed}


def _args_key(args: Mapping[str, Any]) -> str:
    return json.dumps(encode(dict(args)), sort_keys=True, default=str)
