"""A game state: one moment of a run, seen the way search and learning code sees games.

Decisions go through the same tool calls, validation and effects as an LLM agent's, so a game
state and an agent's turn can never disagree about what is legal or what a move does.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..branch import Branch, outcome_index
from ..errors import ContractError, Issue, RunError
from ..returns import seat_returns, seat_rewards
from ..session import END_TURN
from ..snapshot import encode
from ..turn import Turn
from .observe import digest, information_state, observation_struct, observation_text, state_key
from .space import Action, legal_calls

if TYPE_CHECKING:
    from .game import Game

__all__ = ["GameState", "CHANCE", "SIMULTANEOUS", "TERMINAL"]

#: ``current_player()`` at a chance node, a simultaneous node, and the end.
CHANCE, SIMULTANEOUS, TERMINAL = -1, -2, -4

ActionLike = Union[int, str, Action, Mapping[str, Any], Tuple[str, Mapping[str, Any]]]


class GameState:
    """One state of a :class:`~fg_env.sdk.game.Game`. ``apply_action`` changes it; ``child`` and ``clone``
    give new independent states. Players are seat indices (``game.players[i]`` is the entity id)."""

    def __init__(self, game: "Game", branch: Branch, history: List[Dict[str, Any]],
                 previous: Optional[List[float]] = None):
        self.game = game
        self._branch = branch
        self._pilot = branch._pilot
        self._history = history
        self._previous = previous
        self._legal: Dict[int, Tuple[List[Action], Dict[str, str]]] = {}

    # -- whose decision -----------------------------------------------------------------------------

    def is_terminal(self) -> bool:
        if self._pilot.pause is not None:
            return False
        if self._pilot.env.status == "failed":
            raise RunError(f"the game's run failed: {self._pilot.env.error}", "game")
        return True

    def is_chance_node(self) -> bool:
        pause = self._pilot.pause
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
        env = self._pilot.env
        actors = self._pilot.read(lambda: [t.actor.id for t in env.origin.staged if not t.done])
        return [self.game.seat(actor) for actor in actors if actor in self.game.players]

    def chance_outcomes(self) -> List[Tuple[int, float]]:
        """``(outcome, probability)`` for every possible outcome of the chance node."""
        pause = self._pilot.pause
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

    def action_to_string(self, player: int, action: ActionLike) -> str:
        if player == CHANCE:
            pause = self._pilot.pause
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
            pause = self._pilot.pause
            assert pause is not None and pause.node is not None
            index = outcome_index(pause.node, action.id if isinstance(action, Action) and action.id is not None
                                  else action)  # type: ignore[arg-type]
            self._before()
            self._pilot.choose(index)
            self._history.append({"chance": index, "outcome": pause.node.outcomes[index].label})
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
        return GameState(self.game, self._branch.clone(), list(self._history),
                         list(self._previous) if self._previous is not None else None)

    # -- scores --------------------------------------------------------------------------------------

    def returns(self) -> List[float]:
        """Each seat's return so far (the contract's ``game.returns``)."""
        contract = self.game.contract
        if contract.game is None or contract.game.returns is None:
            raise ContractError([Issue("game.returns", "is not declared, so the game has no returns",
                                       'declare what each seat scores, e.g. "game": {"returns": "$actor.chips"}')])
        env = self._pilot.env
        values = self._pilot.read(lambda: seat_returns(contract, env.world, self.game.players))
        return [values[seat] for seat in self.game.players]

    def rewards(self) -> List[float]:
        """Each seat's reward for the last decision: the contract's ``game.rewards``, else the change in returns."""
        contract, env = self.game.contract, self._pilot.env
        declared = self._pilot.read(lambda: seat_rewards(contract, env.world, self.game.players))
        if declared is not None:
            return [declared[seat] for seat in self.game.players]
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
        env = self._pilot.env
        turn = self._turn()
        actions = [{"id": a.id, "tool": a.tool, "args": dict(a.args)} for a in self.legal_tool_calls(player)] \
            if form == "struct" and player in self.acting_players() else None
        actor = env.world.entities[actor_id]
        if form == "text":
            return self._pilot.read(lambda: observation_text(env, actor, turn))
        return self._pilot.read(lambda: observation_struct(env, actor, turn, actions))

    def information_state_string(self, player: int) -> str:
        """Perfect recall of what the seat was told and did, then what it sees now."""
        env = self._pilot.env
        actor = env.world.entities[self.game.players[player]]
        turn = self._turn()
        return str(self._pilot.read(lambda: information_state(env, actor, turn)))

    def information_state(self, player: int) -> str:
        """A key for the seat's information state (equal keys: the seat cannot tell the states apart)."""
        return digest(self.information_state_string(player))

    def state_key(self) -> str:
        """A key for the world and the pending decision (equal keys: the same position)."""
        env = self._pilot.env
        return str(self._pilot.read(lambda: state_key(env, self._pending())))

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
        return self._branch.result()

    def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return self._branch.entity(entity_id)

    def close(self) -> None:
        """Discard the state's run (its thread stops); also done when the state is garbage collected."""
        self._branch.close()

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
        pause = self._pilot.pause
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

    def _legal_for(self, player: Optional[int]) -> Tuple[List[Action], Dict[str, str]]:
        seat = self._seat(player)
        if seat < 0:
            return [], {}
        if seat in self._legal:
            return self._legal[seat]
        env, actor_id, game = self._pilot.env, self.game.players[seat], self.game

        def work() -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, str]]:
            turn = self._seat_turn(actor_id)
            return legal_calls(env, turn, limit=game.limit, dry_run=game.dry_run) if turn is not None else ([], {})

        calls, unlisted = self._pilot.read(work)
        actions = []
        for tool, args in calls:
            action = game.space.action(tool, args)
            if action.id is None and tool not in game.space.parametric:
                raise RunError(f"{action.text} is legal now but has no id: its arguments were not among the values "
                               "listed when the game was created; give `values` as a list, and `min`/`max` that do "
                               "not depend on the state, so every value has an id", f"actions.{tool}")
            actions.append(action)
        actions.sort(key=lambda a: a.id if a.id is not None else -1)
        self._legal[seat] = (actions, unlisted)
        return self._legal[seat]

    def _seat_turn(self, actor_id: str) -> Optional[Turn]:
        turn = self._turn()
        if turn is not None and turn.actor.id == actor_id:
            return turn
        if turn is not None and turn.staged:
            return next((t for t in self._pilot.env.origin.staged if t.actor.id == actor_id and not t.done), None)
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
        listed, unlisted = self._legal_for(seat)
        action_id = self.game.space.encode(tool, args)
        match = next((a for a in listed if (action_id is not None and a.id == action_id)
                      or (a.tool == tool and dict(a.args) == args)), None)
        if match is not None:
            return match.tool, dict(match.args)  # the listed spelling, so histories compare equal
        if tool in unlisted:
            env, actor_id = self._pilot.env, self.game.players[seat]

            def check() -> Optional[str]:
                turn = self._seat_turn(actor_id)
                if turn is None:
                    return "the seat is not acting"
                params, problem = env.actions.validate(turn.actor, tool, args)
                return problem or env.actions.dry_run(turn.actor, tool, params)

            problem = self._pilot.read(check)
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
        result = self._pilot.call(tool, args)
        if result is not None and not result.ok:
            raise RunError(f"the engine refused {Action(None, tool, args).text} after it was found legal: {result.text}",
                           f"actions.{tool}")
        self._history.append({"player": seat, "tool": tool, "args": args})

    def _before(self) -> None:
        contract = self.game.contract
        if contract.game is not None and contract.game.returns is not None:
            self._previous = self.returns()

    def _changed(self) -> None:
        self._legal.clear()

    def _pending(self) -> Dict[str, Any]:
        pause = self._pilot.pause
        if pause is None:
            return {"over": True}
        if pause.kind == "chance" and pause.node is not None:
            return {"chance": pause.node.name, "site": pause.node.site,
                    "outcomes": [[o.label, o.p] for o in pause.node.outcomes]}
        turn = self._turn()
        assert turn is not None
        sealed = {t.actor.id: encode(t.pending) for t in self._pilot.env.origin.staged}
        return {"actor": turn.actor.id, "stage": turn.stage.name, "calls_left": turn.calls_left,
                "actions_left": turn.actions_left, "pending": encode(turn.pending), "used": dict(turn.used),
                "sealed": sealed}
