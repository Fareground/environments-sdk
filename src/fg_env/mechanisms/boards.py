"""Board games: the ``game`` family's ``board`` mode, the ``$board_*`` functions and its ``game`` op actions.

Pieces are entities (props ``owner``, ``kind``, ``cell``, ``moved``); turn state lives in world props
named after the board (``<name>_turn``, ``<name>_result`` …). Everything is read from and written to
the world through its journaled API, so moves are atomic, snapshots resume exactly and previews
never leak. The engine (:mod:`.board_engine`) is pure; this module bridges it to the world.
"""
from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, config_data, family_action, mode, use_key
from ..world import Abort
from ._game import game_section
from .board_engine import Move, Pos, has_line, in_check, legal, make, position_key, render, score
from .board_rules import BoardConfig, Rules, compile_rules, parse_setup

__all__ = ["rules_of", "position_of"]

KEY = "game.board"


# ---------------------------------------------------------------------------
# Rules and positions from the world
# ---------------------------------------------------------------------------

#: Compiled rules per declared config object (kept alive with it, so ids are never reused).
_COMPILED: Dict[int, Tuple[Mapping[str, Any], Rules]] = {}


def rules_of(world: Any, name: Any) -> Rules:
    """The compiled rules of the board mechanism ``name``; raises :class:`MechanismError` when there is none."""
    contract = getattr(world, "contract", None)
    if contract is None:
        raise MechanismError("board functions need a running environment")
    raw = contract.mechanisms.get(name) if isinstance(name, str) else None
    if not isinstance(raw, Mapping) or use_key(raw) != KEY:
        boards = [n for n, m in contract.mechanisms.items() if use_key(m) == KEY]
        raise MechanismError(f"'{name}' is not a declared game board", f"boards: {', '.join(boards) or 'none declared'}")
    cached = _COMPILED.get(id(raw))
    if cached is not None and cached[0] is raw:
        return cached[1]
    rules = compile_rules(name, BoardConfig.model_validate(config_data(raw)))
    if len(_COMPILED) > 256:
        _COMPILED.clear()
    _COMPILED[id(raw)] = (raw, rules)
    return rules


@dataclass
class _State:
    version: int
    fingerprint: Tuple[Any, ...]
    pos: Pos
    legal: Dict[int, List[Move]] = field(default_factory=dict)


_STATES: "weakref.WeakKeyDictionary[Any, Dict[str, _State]]" = weakref.WeakKeyDictionary()


def _prop(world: Any, rules: Rules, key: str) -> Any:
    return world.props.get(f"{rules.name}_{key}")


def _state(world: Any, rules: Rules) -> _State:
    """The current position, rebuilt only when the world changed (pure cache over journaled state)."""
    states = _STATES.setdefault(world, {})
    cached = states.get(rules.name)
    version = world.journal.version
    if cached is not None and cached.version == version:
        return cached
    pieces = world.entities_of(rules.config.piece_type)
    props = tuple(repr(_prop(world, rules, key)) for key in ("turn", "chain", "ep", "ko", "hand"))
    fingerprint = (props, tuple((e.id, e.properties.get("owner"), e.properties.get("kind"), e.properties.get("cell"),
                                 e.properties.get("moved")) for e in pieces))
    if cached is not None and cached.fingerprint == fingerprint:
        cached.version = version
        return cached
    state = _State(version, fingerprint, position_of(world, rules, pieces))
    states[rules.name] = state
    return state


def position_of(world: Any, rules: Rules, pieces: List[Entity]) -> Pos:
    """Build the engine position from piece entities and the board's world props."""
    geo, name = rules.geo, rules.name
    pos = Pos(geo.size, len(rules.sides))
    where = f"{rules.config.piece_type} entities of board '{name}'"
    for entity in pieces:
        props = entity.properties
        cell, owner, kind = str(props.get("cell") or ""), str(props.get("owner")), str(props.get("kind"))
        if not cell:
            continue  # off the board
        if owner not in rules.sides:
            raise RunError(f"{entity.id}.owner is '{owner}', not a side (sides: {', '.join(rules.sides)})", where)
        if kind not in rules.kinds:
            raise RunError(f"{entity.id}.kind is '{kind}', not a piece kind (kinds: {', '.join(rules.kinds)})", where)
        index = geo.index.get(cell)
        if index is None:
            raise RunError(f"{entity.id}.cell '{cell}' is not a cell of the board", where)
        if pos.cells[index] >= 0:
            raise RunError(f"{entity.id} and {pos.ids[pos.cells[index]]} are both on {cell}", where)
        pos.add(rules.sides.index(owner), kind, index, bool(props.get("moved")), entity.id)
    turn = _prop(world, rules, "turn")
    pos.turn = rules.sides.index(turn) if turn in rules.sides else 0
    chain = _prop(world, rules, "chain")
    pos.chain = pos.ids.index(chain) if chain in pos.ids else -1
    ep = _prop(world, rules, "ep") or {}
    if ep.get("cell") in geo.index and ep.get("piece") in pos.ids:
        pos.ep_cell, pos.ep_piece = geo.index[ep["cell"]], pos.ids.index(ep["piece"])
    ko = _prop(world, rules, "ko")
    pos.ko = geo.index.get(ko, -1) if ko else -1
    hand = _prop(world, rules, "hand") or {}
    pos.hand = [dict(hand.get(side) or {}) for side in rules.sides]
    return pos


def _legal(state: _State, rules: Rules, side: int) -> List[Move]:
    moves = state.legal.get(side)
    if moves is None:
        moves = state.legal[side] = legal(rules, state.pos, side)
    return moves


def _side(rules: Rules, player: Any, source: str) -> int:
    key = player.id if isinstance(player, Entity) else player
    if key not in rules.sides:
        raise ExprError(f"'{key}' is not a side of board '{rules.name}' (sides: {', '.join(rules.sides)})", source)
    return rules.sides.index(key)


def _over(world: Any, rules: Rules) -> bool:
    return bool(_prop(world, rules, "result"))


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _board(call: Call) -> Tuple[Any, Rules]:
    world = call.scope.world
    try:
        return world, rules_of(world, call.arg(0))
    except MechanismError as exc:
        raise ExprError(f"${call.name}: {exc}" + (f" — {exc.fix}" if exc.fix else ""), call.source) from None


@function("board_moves(board, player?)",
          "Legal moves of a player (default: the player to move) on a declared board, as move texts: "
          "e2-e4, b1xc3, e7-e8=Q, O-O, d3 (placement). Empty when the game is over or it is not their turn.",
          min_args=1, max_args=2)
def _moves_function(call: Call) -> List[str]:
    world, rules = _board(call)
    state = _state(world, rules)
    side = _side(rules, call.arg(1), call.source) if len(call) > 1 else state.pos.turn
    if _over(world, rules) or side != state.pos.turn:
        return []
    return [m.text for m in _legal(state, rules, side)]


@function("board_render(board, viewer?)",
          "The board as compact text with coordinates, whose turn it is, the last move, check and the result.",
          min_args=1, max_args=2)
def _render_function(call: Call) -> str:
    world, rules = _board(call)
    state = _state(world, rules)
    viewer = _side(rules, call.arg(1), call.source) if len(call) > 1 and _is_side(rules, call.arg(1)) else None
    return "\n".join(render(rules, state.pos) + _status(world, rules, state, viewer))


def _is_side(rules: Rules, value: Any) -> bool:
    return (value.id if isinstance(value, Entity) else value) in rules.sides


@function("board_at(board, cell)", "The piece entity on a cell, or null.", min_args=2, max_args=2)
def _at_function(call: Call) -> Optional[Entity]:
    world, rules = _board(call)
    state = _state(world, rules)
    cell = call.arg(1)
    index = rules.geo.index.get(cell) if isinstance(cell, str) else None
    if index is None:
        raise ExprError(f"$board_at: '{cell}' is not a cell of board '{rules.name}'", call.source)
    slot = state.pos.cells[index]
    return world.entities.get(state.pos.ids[slot]) if slot >= 0 else None


@function("board_cell(board, cell)", "A cell's declared properties (color, region, terrain …) as a map.",
          min_args=2, max_args=2)
def _cell_function(call: Call) -> Dict[str, Any]:
    _, rules = _board(call)
    cell = call.arg(1)
    if not isinstance(cell, str) or cell not in rules.geo.index:
        raise ExprError(f"$board_cell: '{cell}' is not a cell of board '{rules.name}'", call.source)
    return dict(rules.config.cells.get(cell) or {})


@function("board_in_check(board, player)", "True when the player's royal piece is attacked.", min_args=2, max_args=2)
def _check_function(call: Call) -> bool:
    world, rules = _board(call)
    return in_check(rules, _state(world, rules).pos, _side(rules, call.arg(1), call.source))


@function("board_line(board, player, length)", "True when the player has `length` pieces in a row.",
          min_args=3, max_args=3)
def _line_function(call: Call) -> bool:
    world, rules = _board(call)
    length = call.arg(2)
    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        raise ExprError(f"$board_line: length must be a whole number ≥ 1, got {length!r}", call.source)
    return has_line(rules, _state(world, rules).pos, _side(rules, call.arg(1), call.source), length)


@function("board_score(board)", "Each side's score as {side: points}: pieces on the board (plus surrounded area "
          "when the board scores area, plus komi).", min_args=1, max_args=1)
def _score_function(call: Call) -> Dict[str, float]:
    world, rules = _board(call)
    return dict(zip(rules.sides, score(rules, _state(world, rules).pos)))


def _status(world: Any, rules: Rules, state: _State, viewer: Optional[int]) -> List[str]:
    pos, names = state.pos, rules.side_names
    lines = [_legend(rules)]
    if viewer is not None:
        lines.append(f"You play {names[viewer]}.")
    result = _prop(world, rules, "result")
    if result:
        winner = result.get("winner")
        lines.append(f"Game over ({result.get('reason')}): "
                     + (f"{names[rules.sides.index(winner)]} wins." if winner in rules.sides else "draw."))
    else:
        turn = f"{names[pos.turn]} to move"
        if pos.chain >= 0:
            turn += f", continuing the capture with the piece on {rules.geo.names[pos.at[pos.chain]]}"
        if rules.royal and in_check(rules, pos, pos.turn):
            turn += " (in check)"
        lines.append(turn + ".")
    last = _prop(world, rules, "report")
    if last:
        lines.append(f"Last: {last}")
    if rules.place_from == "hand" and rules.place_kinds:
        lines.append("In hand: " + "; ".join(
            f"{names[s]} " + (", ".join(f"{k}×{n}" for k, n in pos.hand[s].items() if n) or "none")
            for s in range(len(rules.sides))))
    if rules.config.score != "none":
        points = score(rules, pos)
        lines.append("Score: " + ", ".join(f"{names[s]} {_number(points[s])}" for s in range(len(rules.sides))) + ".")
    return lines


def _legend(rules: Rules) -> str:
    """What each board symbol means, as briefly as the symbols allow."""
    names, kinds, sides = rules.side_names, rules.kinds, range(len(rules.sides))
    if len(kinds) == 1:
        return "Marks: " + ", ".join(f"{rules.symbols[(s, kinds[0])]} = {names[s]}" for s in sides) + "."
    cased = len(rules.sides) == 2 and all(
        rules.symbols[(0, k)] == rules.symbols[(0, k)].upper() != rules.symbols[(1, k)] == rules.symbols[(0, k)].lower()
        for k in kinds)
    if cased:
        legend = ", ".join(f"{rules.symbols[(0, k)]} {rules.kind_names[k]}" for k in kinds)
        return f"Pieces: {legend} ({names[0]} upper case, {names[1]} lower case)."
    return "Pieces: " + "; ".join(f"{names[s]} " + ", ".join(f"{rules.symbols[(s, k)]} {rules.kind_names[k]}" for k in kinds)
                                  for s in sides) + "."


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# ---------------------------------------------------------------------------
# The game op's board actions
# ---------------------------------------------------------------------------


def _op_rules(runner: Any, name: Any, where: str) -> Rules:
    try:
        return rules_of(runner.world, name)
    except MechanismError as exc:
        raise RunError(str(exc) + (f" — {exc.fix}" if exc.fix else ""), where) from None


def _mover(runner: Any, rules: Rules, state: _State, vars: Dict[str, Any]) -> int:
    world = runner.world
    if _over(world, rules):
        raise Abort("The game is over.")
    actor = vars.get("actor")
    side = state.pos.turn
    if isinstance(actor, Entity) and actor.id in rules.sides and actor.id != rules.sides[side]:
        raise Abort(f"It is not your turn; {rules.side_names[side]} is to move.")
    return side


@family_action("game", ("board",), "move", keys=("text",), required=("text",),
               example='{"game": "chess", "action": "move", "text": "$params.move"}  (play a legal move for the side to '
                       'move: captures, promotion, capture rules, chains, turn, and game-end detection; fails if illegal)')
def _move_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    rules = _op_rules(runner, effect["game"], where)
    world = runner.world
    state = _state(world, rules)
    side = _mover(runner, rules, state, vars)
    text = runner.eval(effect["text"], vars)
    moves = _legal(state, rules, side)
    move = _find(moves, text)
    if move is None:
        listing = ", ".join(m.text for m in moves) or "none"
        raise Abort(f"{text} is not a legal move. Legal moves: {listing}.")
    _play(world, rules, state, move, side)


def _find(moves: List[Move], text: Any) -> Optional[Move]:
    if not isinstance(text, str):
        return None
    wanted = text.strip()
    for move in moves:
        if move.text == wanted:
            return move
    folded = [m for m in moves if m.text.lower() == wanted.lower()]
    return folded[0] if len(folded) == 1 else None


@family_action("game", ("board",), "pass",
               example='{"game": "go", "action": "pass"}  (the side to move passes; enough passes in a row end the game by score)')
def _pass_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    rules = _op_rules(runner, effect["game"], where)
    world = runner.world
    state = _state(world, rules)
    side = _mover(runner, rules, state, vars)
    if state.pos.chain >= 0:
        raise Abort("Finish the capture first; you cannot pass in the middle of it.")
    if not rules.config.allow_pass and _legal(state, rules, side):
        raise Abort("You cannot pass while you have a legal move.")
    after = state.pos.copy()
    after.turn, after.ep_cell, after.ep_piece, after.ko = (side + 1) % len(rules.sides), -1, -1, -1
    _write(world, rules, state.pos, after)
    passes = int(_prop(world, rules, "passes") or 0) + 1
    _set(world, rules, "passes", passes)
    _set(world, rules, "ply", int(_prop(world, rules, "ply") or 0) + 1)
    _set(world, rules, "last", "pass")
    _set(world, rules, "report", f"{rules.side_names[side]} passed.")
    limit = rules.config.passes_end or len(rules.sides)
    if passes >= limit:
        _finish_by_score(world, rules, after, "passes")
    else:
        _next_turn(world, rules, after)


@family_action("game", ("board",), "setup", keys=("position", "turn"), required=("position",),
               example='{"game": "chess", "action": "setup", "position": "$inputs.start", "turn": "black"}  (replace every '
                       'piece with a position — board-symbol rows or {side: {kind: [cells]}} — and restart the game state)')
def _setup_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    rules = _op_rules(runner, effect["game"], where)
    world = runner.world
    spec = runner.eval(effect["position"], vars)
    turn = runner.eval(effect.get("turn"), vars) if "turn" in effect else rules.sides[0]
    turn = turn.id if isinstance(turn, Entity) else turn
    if turn not in rules.sides:
        raise RunError(f"turn '{turn}' is not a side (sides: {', '.join(rules.sides)})", f"{where}.turn")
    try:
        placed = parse_setup(rules, spec, "position")
    except MechanismError as exc:
        raise RunError(f"{exc}" + (f" — {exc.fix}" if exc.fix else ""), f"{where}.position") from None
    for entity in world.entities_of(rules.config.piece_type):
        world.remove(entity)
    for side, kind, cell in placed:
        _create(world, rules, side, kind, rules.geo.names[cell], False, where)
    initial = _initial_props(rules)
    initial["turn"] = turn
    for key, value in initial.items():
        _set(world, rules, key, value)


# ---------------------------------------------------------------------------
# Playing a move: write the new position, then judge it
# ---------------------------------------------------------------------------


def _set(world: Any, rules: Rules, key: str, value: Any) -> None:
    prop = f"{rules.name}_{key}"
    if world.props.get(prop) != value:
        world.set_world(prop, value)


def _create(world: Any, rules: Rules, side: int, kind: str, cell: str, moved: bool, where: str) -> Entity:
    return world.create(rules.config.piece_type, None, f"{rules.side_names[side]} {rules.kind_names[kind]}",
                        {"owner": rules.sides[side], "kind": kind, "cell": cell, "moved": moved}, None, world.scope(), where)


def _write(world: Any, rules: Rules, before: Pos, after: Pos) -> None:
    """Apply the difference between two positions to the piece entities and the board's world props."""
    names = rules.geo.names
    for slot in range(len(after.at)):
        if slot >= len(before.at):
            if after.at[slot] < 0:
                continue  # placed and removed in the same move (suicide)
            created = _create(world, rules, after.owner[slot], after.kind[slot], names[after.at[slot]], True, rules.name)
            after.ids[slot] = created.id
            continue
        entity = world.entities[before.ids[slot]]
        if after.at[slot] < 0:
            if before.at[slot] >= 0:
                world.remove(entity)
            continue
        if after.at[slot] != before.at[slot]:
            world.set_prop(entity, "cell", names[after.at[slot]])
        if after.owner[slot] != before.owner[slot]:
            world.set_prop(entity, "owner", rules.sides[after.owner[slot]])
        if after.kind[slot] != before.kind[slot]:
            world.set_prop(entity, "kind", after.kind[slot])
        if after.moved[slot] != before.moved[slot]:
            world.set_prop(entity, "moved", after.moved[slot])
    _set(world, rules, "turn", rules.sides[after.turn])
    _set(world, rules, "chain", after.ids[after.chain] if after.chain >= 0 else "")
    _set(world, rules, "ep", {"cell": names[after.ep_cell], "piece": after.ids[after.ep_piece]} if after.ep_cell >= 0 else {})
    _set(world, rules, "ko", names[after.ko] if after.ko >= 0 else "")
    if rules.place_from == "hand" and rules.place_kinds:
        _set(world, rules, "hand", {side: dict(after.hand[i]) for i, side in enumerate(rules.sides)})


def _play(world: Any, rules: Rules, state: _State, move: Move, side: int) -> None:
    before = state.pos
    history = list(_prop(world, rules, "history") or []) if rules.config.repetition else []
    if rules.config.repetition and not history:
        history.append(position_key(rules, before, _legal(state, rules, side)))
    played = make(rules, before, move, side)
    after = played.pos
    _write(world, rules, before, after)
    kind = before.kind[move.piece] if move.piece >= 0 else move.place
    reset = played.captured > 0 or kind in rules.irreversible
    quiet = 0 if reset else int(_prop(world, rules, "quiet") or 0) + 1
    _set(world, rules, "quiet", quiet)
    _set(world, rules, "passes", 0)
    _set(world, rules, "ply", int(_prop(world, rules, "ply") or 0) + 1)
    _set(world, rules, "last", move.text)
    report = f"{rules.side_names[side]} played {move.text}"
    if played.captured > len(move.captures):
        report += f", capturing {played.captured}"
    if played.flipped:
        report += f", flipping {played.flipped}"
    if played.continues:
        report += f"; {rules.side_names[side]} must keep capturing with the piece on {rules.geo.names[after.at[played.piece]]}"
    elif rules.royal and in_check(rules, after, after.turn):
        report += f"; {rules.side_names[after.turn]} is in check"
    _set(world, rules, "report", report + ".")
    if played.continues:
        return
    moves = legal(rules, after, after.turn)
    if rules.config.repetition:
        history = [] if reset else history
        history.append(position_key(rules, after, moves))
        _set(world, rules, "history", history)
    _judge(world, rules, before, after, side, moves, history, quiet)


def _judge(world: Any, rules: Rules, before: Pos, after: Pos, mover: int, moves: List[Move],
           history: List[str], quiet: int) -> None:
    ending = _ending(rules, before, after, mover, moves, history, quiet)
    if ending == "pass":
        _next_turn(world, rules, after, moves)
    elif ending is not None:
        _finish(world, rules, after, ending[0], ending[1])


def _ending(rules: Rules, before: Pos, after: Pos, mover: int, moves: List[Move], history: List[str],
            quiet: int) -> Any:
    """How the game ends after a move: (reason, winner side or None), "pass" for a forced pass, or None."""
    config = rules.config
    for other in range(len(rules.sides)):
        if other != mover and rules.royal and _royals(rules, before, other) and not _royals(rules, after, other):
            return "royal_captured", mover
    if config.line is not None:
        for side in [mover] + [s for s in range(len(rules.sides)) if s != mover]:
            if has_line(rules, after, side, config.line):
                return "line", side
    if not moves and not config.allow_pass:
        if config.no_moves == "pass":
            return "pass"
        stuck = after.turn
        if config.self_check and _royals(rules, after, stuck):
            if in_check(rules, after, stuck):
                return "checkmate", _opponent(rules, stuck, mover)
            return "stalemate", _opponent(rules, stuck, mover) if config.stalemate == "lose" else None
        return "no_moves", _opponent(rules, stuck, mover) if config.no_moves == "lose" else None
    if config.repetition and history and history.count(history[-1]) >= config.repetition:
        return "repetition", None
    if config.move_limit and quiet >= config.move_limit:
        return "move_limit", None
    return None


def _next_turn(world: Any, rules: Rules, after: Pos, moves: Optional[List[Move]] = None) -> None:
    """Pass automatically for every side that has no legal move (``no_moves: pass``); end by score when none can move."""
    if rules.config.no_moves != "pass" or rules.config.allow_pass:
        return
    current = after
    for _ in range(len(rules.sides)):
        available = moves if moves is not None and current is after else legal(rules, current, current.turn)
        if available:
            if current is not after:
                _write(world, rules, after, current)
            return
        world.emit(rules.name, f"{rules.side_names[current.turn]} has no legal move and passes.",
                   data={"mechanism": "board", "pass": rules.sides[current.turn]})
        current = current.copy()
        current.turn = (current.turn + 1) % len(rules.sides)
        current.ep_cell = current.ep_piece = current.ko = -1
    _finish_by_score(world, rules, current, "no_moves")


def _royals(rules: Rules, pos: Pos, side: int) -> bool:
    return any(pos.kind[p] in rules.royal for p in pos.pieces(side))


def _opponent(rules: Rules, loser: int, mover: int) -> int:
    return mover if mover != loser else (loser + 1) % len(rules.sides)


def _finish_by_score(world: Any, rules: Rules, pos: Pos, reason: str) -> None:
    points = score(rules, pos)
    best = max(points)
    leaders = [s for s, p in enumerate(points) if p == best]
    winner = leaders[0] if rules.config.score != "none" and len(leaders) == 1 else None
    _finish(world, rules, pos, reason, winner, points)


def _finish(world: Any, rules: Rules, pos: Pos, reason: str, winner: Optional[int],
            points: Optional[List[float]] = None) -> None:
    names = rules.side_names
    if points is None and rules.config.score != "none":
        points = score(rules, pos)
    result: Dict[str, Any] = {"winner": rules.sides[winner] if winner is not None else None, "reason": reason}
    if points is not None:
        result["score"] = {side: int(p) if float(p).is_integer() else p for side, p in zip(rules.sides, points)}
    _set(world, rules, "result", result)
    outcome = f"{names[winner]} wins" if winner is not None else "Draw"
    detail = reason.replace("_", " ")
    if points is not None and rules.config.score != "none":
        detail += "; " + ", ".join(f"{names[s]} {_number(p)}" for s, p in enumerate(points))
    world.request_end(reason, rules.sides[winner] if winner is not None else None, f"{outcome} ({detail}).")


# ---------------------------------------------------------------------------
# The board mode
# ---------------------------------------------------------------------------


def _initial_props(rules: Rules) -> Dict[str, Any]:
    return {"turn": rules.sides[0], "chain": "", "ep": {}, "ko": "",
            "hand": {side: dict(rules.config.hand.get(side) or {}) for side in rules.sides},
            "ply": 0, "quiet": 0, "passes": 0, "history": [], "last": "", "report": "", "result": {}}


_DOC = """Abstract board games as data: a board (grid, hex, ring, graph), seated players, piece kinds with movement
rules, and native legal-move generation, capture rules and game-end detection. Generates a `<name>_move` tool
whose `move` argument lists only legal moves (e2-e4, b1xc3, e7-e8=Q, O-O, d3 for a placement), a
`<name>_pass` tool when passing is allowed, a turn stage (one ply per round; a capture chain continues in the
same turn), a `<name>_board` view, pieces as `piece_type` entities (owner, kind, cell, moved) and world props
`<name>_turn`, `_last`, `_ply`, `_result` ({winner, reason, score}). The game ends itself with reasons
checkmate, stalemate, no_moves, royal_captured, line, repetition, move_limit or passes.

Piece moves (`pieces.K.moves`): `{"step": dirs, "distance": 2, "only": "move", "first": true, "passable": true}`,
`{"slide": "diagonal", "max": n}`, `{"leap": [2, 1]}` (all mirrors), `{"jump": "diagonal", "over": "enemy",
"chain": true}`; also `from`/`within` zones, `en_passant`. Directions: grid n ne e se s sw w nw, groups
orthogonal diagonal all, relative to the side's forward f fr r br b bl l fl; hex ne e se sw w nw; ring cw ccw;
graph edge labels; `"step": "adjacent"` moves to any edge-sharing neighbour (graphs). Promotion:
`"promote": {"zone": "far", "to": ["Q", "R", "B", "N"]}`. Capture rules (`captures`): custodial, flip (Othello),
enclose (Go: liberties, suicide, ko). Setup rows use the board symbols: first side upper case, second lower
case (or each side's `mark` when there is one kind); the `setup` action loads a position mid-run. Limits: one
piece per cell (no stacks), dice are not built in."""


@mode("game", "board", BoardConfig, _DOC, example={
    "size": [3, 3], "sides": ["x", "o"], "pieces": {"mark": {}}, "place": {}, "line": 3, "no_moves": "draw"},
      ends=lambda config: True)
def _expand_board(name: str, config: BoardConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    rules = compile_rules(name, config)
    _check_shared_types(name, config, contract)
    players, piece_type = config.who, config.piece_type
    types = contract.get("types") or {}
    if players in types and not types[players].get("agent") and not types[players].get("extends"):
        raise MechanismError(f"who '{players}' is not an agent type", "set \"agent\": true on it", "who")
    entities: Dict[str, Any] = {}
    declared = contract.get("entities") or {}
    for index, side_id in enumerate(rules.sides):
        if side_id in declared and declared[side_id].get("type") != players:
            raise MechanismError(f"entity '{side_id}' is a {declared[side_id].get('type')}, not a {players}",
                                 f"make it type {players} or set `who`", "sides")
        entities[side_id] = {"type": players, "name": rules.side_names[index]}
    counters: Dict[Tuple[int, str], int] = {}
    for side, kind, cell in parse_setup(rules, config.setup):
        counters[(side, kind)] = counters.get((side, kind), 0) + 1
        label = rules.kind_names[kind].lower().replace(" ", "_")
        entities[f"{rules.sides[side]}_{label}_{counters[(side, kind)]}"] = {
            "type": piece_type, "name": f"{rules.side_names[side]} {rules.kind_names[kind]}",
            "props": {"owner": rules.sides[side], "kind": kind, "cell": rules.geo.names[cell]}}
    initial = _initial_props(rules)
    world = {f"{name}_{key}": {"default": value, "type": _prop_type(value)} for key, value in initial.items()}
    move, pass_ = f"{name}_move", f"{name}_pass"
    report = f"{{$world.{name}_report}}"
    turn = f"$world.{name}_turn"
    actions: Dict[str, Any] = {move: {
        "by": players, "description": _move_help(rules),
        "params": {"move": {"type": "enum", "values": f"$board_moves('{name}', $actor)", "description": "One of your legal moves."}},
        "when": [{"expr": f"{turn} == $actor.id", "why": "It is not your turn."},
                 {"expr": f"$len($board_moves('{name}', $actor)) > 0", "why": "You have no legal move."}],
        "do": [{"game": name, "action": "move", "text": "$params.move"}],
        "outcome": report, "announce": report, "terminal": f"{turn} != $actor.id"}}
    if config.allow_pass:
        actions[pass_] = {"by": players, "description": "Pass instead of moving.",
                          "when": [{"expr": f"{turn} == $actor.id and $world.{name}_chain == ''", "why": "You cannot pass now."}],
                          "do": [{"game": name, "action": "pass"}], "outcome": report, "announce": report, "terminal": True}
    chain_turn = max(2, rules.geo.size // 2) if rules.chains else 1
    fragment: Dict[str, Any] = {
        "types": {players: {"agent": True, "description": "A player at the board."},
                  piece_type: {"description": f"A piece on the {name} board.", "inspect": False, "props": {
                      "owner": {"type": "text", "default": "", "description": "Id of the side that owns it."},
                      "kind": {"type": "text", "default": "", "description": "Piece kind."},
                      "cell": {"type": "text", "default": "", "description": "Cell it stands on."},
                      "moved": {"type": "bool", "default": False, "description": "Whether it has moved."}}}},
        "entities": entities,
        "world": world,
        "actions": actions,
        "views": {f"{name}_board": {"for": players, "bullet": False,
                                    "show": f"Board:\n{{$board_render('{name}', $actor)}}"}},
        "outputs": {f"{name}_result": {"expr": f"$world.{name}_result", "type": "map",
                                       "description": "{winner, reason, score} once the game has ended."}},
    }
    fragment.update(game_section(contract, _game(name, rules, players)))
    names = list(actions)
    if config.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "who": f"$it.id == {turn}", "actions": names,
                               "max_actions": chain_turn, "max_calls": chain_turn + 6, "must_act": True,
                               "brief": "Your move."}]
    else:
        fragment["stage_hooks"] = {config.stage: {"actions": names}}
    return fragment


def _game(name: str, rules: Rules, players: str) -> Dict[str, Any]:
    """Seats in side order; the winner scores one point from every other side, so the returns always add up to zero."""
    sides = "[" + ", ".join(f"'{side}'" for side in rules.sides) + "]"
    winner = f"$get($world.{name}_result, 'winner', null)"
    return {"players": players, "seat": f"$index({sides}, $it.id)", "utility": "zero_sum",
            "returns": f"0 if {winner} == null or not ($actor.id in {sides}) "
                       f"else ({len(rules.sides) - 1} if {winner} == $actor.id else -1)"}


def _prop_type(value: Any) -> str:
    return {bool: "bool", int: "int", str: "text", dict: "map", list: "list"}[type(value)]


def _move_help(rules: Rules) -> str:
    parts = []
    if any(rules.patterns[s][k] for s in range(len(rules.sides)) for k in rules.kinds):
        parts.append("a move is written from-to (e2-e4) and a capture fromxto (d4xe5)")
    if any(len(p.to) > 1 or p.optional for side in rules.promotions for p in side.values()):
        parts.append("a promotion adds =kind (e7-e8=Q)")
    if rules.castles:
        parts.append("castling is " + " / ".join(dict.fromkeys(c.text for c in rules.castles)))
    if rules.place_kinds:
        parts.append("placing a piece is its cell (d3)" if len(rules.place_kinds) == 1 else "placing is kind@cell (P@e4)")
    text = "Make your move: " + "; ".join(parts) + "." if parts else "Make your move."
    if rules.chains:
        text += " After a capture that can continue, the same piece must keep capturing."
    return text


def _check_shared_types(name: str, config: BoardConfig, contract: Mapping[str, Any]) -> None:
    for other, raw in (contract.get("mechanisms") or {}).items():
        if other == name or use_key(raw) != KEY:
            continue
        if raw.get("piece_type", "piece") == config.piece_type:
            raise MechanismError(f"boards '{other}' and '{name}' both use the piece type '{config.piece_type}'",
                                 "give each board its own piece_type", "piece_type")
