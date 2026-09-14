"""Betting and pots: no-limit betting rounds with blinds, antes and minimum-raise rules, all-ins, side
pots and showdown distribution — the ``pot`` mechanism and its native ops.

Chips live on the players (``stack``, ``bet`` this betting round, ``committed`` this hand) and the
table state in world props ``<pot>_*``, so every wager is journaled, atomic and snapshotted.
``$sum(player, $it.stack + $it.committed)`` never changes: the mechanism adds that invariant.

Rules implemented: the button moves to the next player with chips each hand; heads-up the button
posts the small blind and acts first before the flop; a raise must be at least the last full raise
(never less than the minimum bet); an all-in for less than a full raise does not reopen betting to
players who already acted; side pots are built from each player's committed chips and each is won
by the best eligible score; split pots give odd chips to the winners closest to the button's left.
Not implemented: pot-limit and fixed-limit sizing, and reopening by several short all-ins together.
"""
from __future__ import annotations

from typing import Any, cast, Dict, List, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, is_expr
from ..registry import MechanismError, effect_op, mechanism
from ..world import Abort
from .contract_cache import parse_kind, per_contract

__all__ = ["PotConfig", "side_pots"]

def _props(entity: Entity) -> Dict[str, Any]:
    """An entity's properties, typed loosely: values are whatever the contract declared."""
    return cast(Dict[str, Any], entity.properties)


MOVES = ("fold", "check", "call", "bet", "raise", "all_in", "timeout")
ACTIONS = ("fold", "check", "call", "bet", "raise", "all_in")


class PotConfig(BaseModel):
    """A poker-style table: betting rounds (streets) played every round (one hand per round)."""

    model_config = ConfigDict(extra="forbid")

    players: str = Field(..., description="Agent type that bets (subtypes included).")
    stack: Union[int, str] = Field(1000, description="Starting chips (number or expression).")
    seat: Optional[str] = Field(None, description="Seat order: an expression over $it, lowest first (default: declaration order).")
    blinds: Optional[List[Union[int, str]]] = Field(None, description="[small, big] blinds posted each hand (numbers or expressions).")
    ante: Union[int, str] = Field(0, description="Chips every player puts in before the deal.")
    min_bet: Optional[Union[int, str]] = Field(None, description="Smallest bet or raise size (default: the big blind, else 1).")
    streets: Dict[str, List[Any]] = Field(
        default_factory=lambda: {"betting": []},
        description="Betting rounds in order: {stage name: effects run before that round's betting (deal the flop …)}.")
    setup: List[Any] = Field(default_factory=list, description="Effects at the start of each hand, before blinds (collect and deal cards).")
    before_showdown: List[Any] = Field(default_factory=list, description="Effects before a contested showdown (reveal hands).")
    score: str = Field(..., description="A player's showdown score ($it), higher wins, e.g. \"$poker_rank($hand($it) + $zone(board)).score\".")
    label: Optional[str] = Field(None, description="Text naming a player's holding at showdown ($it), e.g. \"$poker_rank(...).name\".")
    max_raises: Optional[int] = Field(None, ge=1, description="Bets and raises allowed per betting round (default unlimited).")
    max_calls: int = Field(6, ge=1, description="Tool calls per betting turn.")
    conserve: bool = Field(True, description="Add the invariant that chips are never created or destroyed.")
    views: bool = Field(True, description="Generate the table view.")


# ---------------------------------------------------------------------------
# Config and seats of a running contract
# ---------------------------------------------------------------------------

def _tables(world: Any) -> Dict[str, PotConfig]:
    return per_contract(world, "pot", lambda contract: parse_kind(contract, "pot", PotConfig), {})


def _table(world: Any, name: Any, where: str) -> PotConfig:
    tables = _tables(world)
    if not isinstance(name, str) or name not in tables:
        raise RunError(f"'{name}' is not a declared pot (pots: {', '.join(tables) or 'none'})", where)
    return tables[name]


def _seats(world: Any, config: PotConfig, where: str) -> List[Entity]:
    players = list(world.entities_of(config.players))
    if config.seat is None:
        return players
    key = compile_expr(config.seat)
    try:
        keyed = [(key(world.scope(it=p, i=i)), i, p) for i, p in enumerate(players)]
        keyed.sort(key=lambda t: (t[0], t[1]))
    except (ExprError, TypeError) as exc:
        raise RunError(f"seat: {exc}", where) from None
    return [p for _, _, p in keyed]


def _chips(world: Any, raw: Any, where: str) -> int:
    value = compile_expr(raw)(world.scope()) if is_expr(raw) else raw
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or float(value) != int(value):
        raise RunError(f"must be a whole number of chips ≥ 0, got {value!r}", where)
    return int(value)


def _min_bet(world: Any, config: PotConfig, where: str) -> int:
    if config.min_bet is not None:
        return max(1, _chips(world, config.min_bet, where))
    if config.blinds:
        return max(1, _chips(world, config.blinds[1], where))
    return 1


def _p(player: Entity, prop: str) -> Any:
    return _props(player)[prop]


def _live(player: Entity) -> bool:
    return bool(_p(player, "in_hand")) and not _p(player, "folded")


def _after(seats: List[Entity], anchor: Optional[Entity]) -> List[Entity]:
    """Seats starting just left of ``anchor`` (the anchor last)."""
    if anchor is None or anchor not in seats:
        return list(seats)
    start = seats.index(anchor) + 1
    return seats[start:] + seats[:start]


def _put(world: Any, player: Entity, chips: int, into_bet: bool = True) -> int:
    chips = min(chips, _p(player, "stack"))
    if chips <= 0:
        return 0
    world.set_prop(player, "stack", _p(player, "stack") - chips)
    world.set_prop(player, "committed", _p(player, "committed") + chips)
    if into_bet:
        world.set_prop(player, "bet", _p(player, "bet") + chips)
    return chips


def _next_to_act(world: Any, config: PotConfig, name: str, anchor: Optional[Entity], where: str) -> str:
    seats = _seats(world, config, where)
    live = [p for p in seats if _live(p)]
    if len(live) <= 1:
        return ""
    current = world.props[f"{name}_current_bet"]
    with_chips = sum(1 for p in live if _p(p, "stack") > 0)
    for player in _after(seats, anchor):
        if not _live(player) or _p(player, "stack") <= 0:
            continue
        if _p(player, "bet") < current or (not _p(player, "acted") and with_chips >= 2):
            return player.id
    return ""


def options(world: Any, config: PotConfig, name: str, player: Entity) -> Dict[str, Any]:
    """What ``player`` may do now and for how much."""
    props = _props(player)
    current = world.props[f"{name}_current_bet"]
    stack, bet = props["stack"], props["bet"]
    to_call = max(0, current - bet)
    turn = world.props[f"{name}_to_act"] == player.id and _live(player)
    contested = any(_live(q) and _p(q, "stack") > 0 for q in world.entities_of(config.players) if q.id != player.id)
    room = config.max_raises is None or world.props[f"{name}_raises"] < config.max_raises
    min_bet = _min_bet(world, config, f"mechanisms.{name}.min_bet")
    can_bet = turn and current == 0 and stack > 0 and contested and room
    can_raise = turn and current > 0 and not props["acted"] and stack > to_call and contested and room
    return {"your_turn": turn, "to_call": to_call, "call_amount": min(to_call, stack), "current_bet": current,
            "can_check": turn and to_call == 0, "can_call": turn and to_call > 0 and stack > 0,
            "can_bet": can_bet, "min_bet": min(min_bet, stack),
            "can_raise": can_raise, "min_raise_to": min(current + world.props[f"{name}_min_raise"], bet + stack),
            "max_to": bet + stack, "can_all_in": (can_raise or can_bet) and stack > to_call,
            "pot": sum(_p(p, "committed") for p in world.entities_of(config.players))}


# ---------------------------------------------------------------------------
# Hands, betting rounds, wagers
# ---------------------------------------------------------------------------


def new_hand(world: Any, config: PotConfig, name: str, where: str) -> None:
    seats = _seats(world, config, where)
    for player in seats:
        if _p(player, "committed"):  # an unfinished hand: its chips go back to their owners
            world.set_prop(player, "stack", _p(player, "stack") + _p(player, "committed"))
            world.set_prop(player, "committed", 0)
        for prop, value in (("in_hand", _p(player, "stack") > 0), ("folded", False), ("acted", False), ("bet", 0)):
            world.set_prop(player, prop, value)
    button = world.entities.get(world.props[f"{name}_button"])
    nxt = next((p for p in _after(seats, button) if _p(p, "in_hand")), None)
    world.set_world(f"{name}_button", nxt.id if nxt is not None else "")
    world.set_world(f"{name}_hands", world.props[f"{name}_hands"] + 1)
    for prop, value in (("current_bet", 0), ("raises", 0), ("to_act", ""), ("result", {}), ("last", {}),
                        ("min_raise", _min_bet(world, config, where)), ("street", "")):
        world.set_world(f"{name}_{prop}", value)


def post_blinds(world: Any, config: PotConfig, name: str, where: str) -> None:
    seats = _seats(world, config, where)
    button = world.entities.get(world.props[f"{name}_button"])
    active = [p for p in _after(seats, button) if _p(p, "in_hand")]
    if button in active:
        active = [button] + [p for p in active if p is not button]
    if len(active) < 2:
        return
    ante = _chips(world, config.ante, f"{where}.ante")
    for player in active:
        _put(world, player, ante, into_bet=False)
    if not config.blinds:
        return
    small, big = (_chips(world, b, f"{where}.blinds") for b in config.blinds)
    sb, bb = (active[0], active[1]) if len(active) == 2 else (active[1], active[2])
    posted = (_put(world, sb, small), _put(world, bb, big))
    world.set_world(f"{name}_current_bet", big)
    world.set_world(f"{name}_min_raise", max(big, _min_bet(world, config, where)))
    world.set_world(f"{name}_to_act", _next_to_act(world, config, name, bb, where))
    lead = f"Hand {world.props[f'{name}_hands']}: " + (f"{button.name} has the button; " if button is not None else "")
    world.emit(name, f"{lead}{sb.name} posts {posted[0]}, {bb.name} posts {posted[1]}.")


def open_betting(world: Any, config: PotConfig, name: str, where: str) -> None:
    for player in _seats(world, config, where):
        world.set_prop(player, "bet", 0)
        world.set_prop(player, "acted", False)
    for prop, value in (("current_bet", 0), ("raises", 0), ("min_raise", _min_bet(world, config, where)),
                        ("street", world.stage or "")):
        world.set_world(f"{name}_{prop}", value)
    button = world.entities.get(world.props[f"{name}_button"])
    world.set_world(f"{name}_to_act", _next_to_act(world, config, name, button, where))


def wager(world: Any, config: PotConfig, name: str, player: Entity, move: str, amount: Any, where: str) -> int:
    """Apply one betting move for ``player``; returns the chips added. Illegal moves abort the action."""
    if move not in MOVES:
        raise RunError(f"action must be one of {', '.join(MOVES)}, got {move!r}", where)
    if world.props[f"{name}_to_act"] != player.id or not _live(player):
        raise Abort("It is not your turn to bet.")
    opts = options(world, config, name, player)
    stack, bet, current = _p(player, "stack"), _p(player, "bet"), opts["current_bet"]
    if move == "timeout":
        move = "check" if opts["to_call"] == 0 else "fold"
    target = bet
    if move == "fold":
        world.set_prop(player, "folded", True)
    elif move == "check" and opts["to_call"] > 0:
        raise Abort(f"There is a bet of {opts['to_call']} to call: call, raise or fold.")
    elif move == "call":
        if opts["to_call"] == 0:
            raise Abort("There is nothing to call: check instead.")
        target = bet + opts["call_amount"]
    elif move == "all_in":
        target = bet + stack
        if target > current and not (opts["can_raise"] or opts["can_bet"]):
            raise Abort("You may not raise now: call or fold.")
    elif move in ("bet", "raise"):
        target = _sized(opts, move, amount, bet, stack)
    added = target - bet
    if target > current:
        size = target - current
        if size >= world.props[f"{name}_min_raise"]:
            world.set_world(f"{name}_min_raise", size)
            for other in world.entities_of(config.players):
                if other is not player and _live(other) and _p(other, "stack") > 0:
                    world.set_prop(other, "acted", False)
        world.set_world(f"{name}_current_bet", target)
        world.set_world(f"{name}_raises", world.props[f"{name}_raises"] + 1)
    _put(world, player, added)
    world.set_prop(player, "acted", True)
    world.set_world(f"{name}_last", {"player": player.id, "move": move, "amount": added, "to": _p(player, "bet")})
    world.set_world(f"{name}_to_act", _next_to_act(world, config, name, player, where))
    return added


def _sized(opts: Mapping[str, Any], move: str, amount: Any, bet: int, stack: int) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise Abort(f"{move} needs a whole number of chips, got {amount!r}.")
    if move == "bet":
        if not opts["can_bet"]:
            raise Abort("You cannot bet now" + (": there is already a bet to call or raise." if opts["current_bet"] else "."))
        if amount > stack or (amount < opts["min_bet"] and amount != stack):
            raise Abort(f"Bet between {opts['min_bet']} and {stack} chips.")
        return bet + amount
    if not opts["can_raise"]:
        raise Abort("You cannot raise now: call or fold." if opts["current_bet"] else "There is no bet to raise: bet instead.")
    if amount > opts["max_to"] or (amount < opts["min_raise_to"] and amount != opts["max_to"]):
        raise Abort(f"Raise to between {opts['min_raise_to']} and {opts['max_to']} chips in total.")
    return amount


def side_pots(committed: Mapping[str, int], live: List[str]) -> List[Tuple[int, List[str]]]:
    """``[(amount, eligible ids)]``, main pot first, from each player's committed chips. Folded players'
    chips count toward the pots they reached; chips above every live player's level join the last pot."""
    levels = sorted({committed.get(pid, 0) for pid in live if committed.get(pid, 0) > 0})
    pots: List[Tuple[int, List[str]]] = []
    previous = 0
    for level in levels:
        amount = sum(min(c, level) - min(c, previous) for c in committed.values())
        if amount:
            pots.append((amount, [pid for pid in live if committed.get(pid, 0) >= level]))
        previous = level
    extra = sum(c - min(c, previous) for c in committed.values())
    if extra:
        if pots:
            pots[-1] = (pots[-1][0] + extra, pots[-1][1])
        else:
            pots.append((extra, list(live)))
    return pots


def showdown(runner: Any, config: PotConfig, name: str, vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    seats = _seats(world, config, where)
    button = world.entities.get(world.props[f"{name}_button"])
    order = _after(seats, button)  # odd chips go to winners nearest the button's left
    live = [p for p in order if _live(p)]
    committed = {p.id: _p(p, "committed") for p in seats if _p(p, "committed") > 0}
    if not committed:
        return
    payouts: Dict[str, int] = {}
    scores: Dict[str, Any] = {}
    labels: Dict[str, str] = {}
    if len(live) > 1:
        for player in live:
            scores[player.id] = runner.eval(config.score, {**vars, "it": player})
            if config.label:
                labels[player.id] = runner.text("{" + config.label + "}", {**vars, "it": player})
    pots = side_pots(committed, [p.id for p in live]) if live else [(sum(committed.values()), [])]
    record = []
    for amount, eligible in pots:
        if not eligible:  # nobody left in the hand: chips go back to whoever put them in
            for pid, chips in committed.items():
                payouts[pid] = payouts.get(pid, 0) + chips
            continue
        try:
            best = max(scores[pid] for pid in eligible) if scores else None
        except TypeError:
            raise RunError("score must give comparable values (numbers)", f"mechanisms.{name}.score") from None
        winners = [pid for pid in eligible if not scores or scores[pid] == best]
        share, odd = divmod(amount, len(winners))
        for index, pid in enumerate(winners):
            payouts[pid] = payouts.get(pid, 0) + share + (1 if index < odd else 0)
        record.append({"amount": amount, "eligible": eligible, "winners": winners})
    for player in seats:
        world.set_prop(player, "stack", _p(player, "stack") + payouts.get(player.id, 0))
        world.set_prop(player, "committed", 0)
        world.set_prop(player, "bet", 0)
    world.set_world(f"{name}_to_act", "")
    world.set_world(f"{name}_result", {"hand": world.props[f"{name}_hands"], "pots": record, "payouts": payouts,
                                       "uncontested": len(live) == 1})
    world.emit(name, _showdown_text(world, record, labels, len(live) == 1))


def _showdown_text(world: Any, pots: List[Dict[str, Any]], labels: Mapping[str, str], uncontested: bool) -> str:
    if uncontested and pots:
        winner = world.entities[pots[0]["winners"][0]].name
        return f"{winner} wins the pot ({sum(p['amount'] for p in pots)}); everyone else folded."
    contested = [pot for pot in pots if len(pot["eligible"]) > 1]
    parts = []
    for index, pot in enumerate(contested):
        title = "the pot" if len(contested) == 1 else ("the main pot" if index == 0 else f"side pot {index}")
        names = " and ".join(world.entities[w].name + (f" ({labels[w]})" if w in labels else "") for w in pot["winners"])
        verb = "split" if len(pot["winners"]) > 1 else "wins"
        parts.append(f"{names} {verb} {title} ({pot['amount']})")
    for pot in pots:
        if len(pot["eligible"]) == 1:  # chips nobody could match go back to their owner
            parts.append(f"{world.entities[pot['winners'][0]].name} takes back {pot['amount']} uncalled chips")
    return "Showdown: " + "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Ops and functions
# ---------------------------------------------------------------------------


def _pot_check(key: str) -> Any:
    def check(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
        names = [n for n, use in checker.c.mechanisms.items() if isinstance(use, Mapping) and use.get("kind") == "pot"]
        if effect.get(key) not in names:
            return [(f"{path}.{key}", f"'{effect.get(key)}' is not a declared pot", f"pots: {', '.join(names) or 'none'}")]
        move = effect.get("action")
        if key == "wager" and isinstance(move, str) and not is_expr(move) and move not in MOVES:
            return [(f"{path}.action", f"'{move}' is not a betting action", f"actions: {', '.join(MOVES)}")]
        return []
    return check


def _simple_op(op: str, run: Any, example: str) -> None:
    @effect_op(op, keys=(), check=_pot_check(op), example=example)
    def _op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        run(runner.world, _table(runner.world, effect[op], where), effect[op], where)


_simple_op("new_hand", new_hand, '{"new_hand": "table"}  (start a hand: reset bets and folds, move the button; generated)')
_simple_op("post_blinds", post_blinds, '{"post_blinds": "table"}  (antes and blinds, first player to act; generated)')
_simple_op("open_betting", open_betting, '{"open_betting": "table"}  (a new betting round from the button\'s left; generated)')


@effect_op("wager", keys=("action", "amount", "player"), required=("action",), check=_pot_check("wager"),
           example='{"wager": "table", "action": "raise", "amount": "$params.to"}  (fold check call bet raise all_in timeout; '
                   'for $actor or `player`; an illegal move fails the action)')
def _wager_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    config = _table(world, effect["wager"], where)
    player = world.entity(runner.eval(effect["player"], vars) if "player" in effect else vars.get("actor"))
    if player is None:
        raise RunError("wager needs a player (`player`, default $actor)", where)
    move = runner.eval(effect["action"], vars) if is_expr(effect["action"]) else effect["action"]
    wager(world, config, effect["wager"], player, str(move), runner.eval(effect.get("amount"), vars), where)


@effect_op("showdown", keys=(), check=_pot_check("showdown"),
           example='{"showdown": "table"}  (pay every pot and side pot to its best eligible score; generated)')
def _showdown_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    showdown(runner, _table(runner.world, effect["showdown"], where), effect["showdown"], vars, where)


def _function_table(call: Call) -> Tuple[str, PotConfig]:
    name = call.arg(0)
    tables = _tables(call.scope.world)
    if name not in tables:
        raise ExprError(f"${call.name}: '{name}' is not a declared pot (pots: {', '.join(tables) or 'none'})", call.source)
    return name, tables[name]


@function("pot_options(pot, player)",
          "What a player may do in a betting round: {your_turn, to_call, call_amount, can_check, can_call, can_bet, "
          "min_bet, can_raise, min_raise_to, max_to, can_all_in, current_bet, pot}.", min_args=2, max_args=2)
def _pot_options_function(call: Call) -> Dict[str, Any]:
    name, config = _function_table(call)
    player = call.scope.world.entity(call.arg(1))
    if player is None:
        raise ExprError(f"$pot_options: expected a player, got {call.arg(1)!r}", call.source)
    return options(call.scope.world, config, name, player)


@function("pot_live(pot)", "Players still in the hand (not folded), in seat order.", min_args=1, max_args=1)
def _pot_live_function(call: Call) -> List[Entity]:
    name, config = _function_table(call)
    return [p for p in _seats(call.scope.world, config, name) if _live(p)]


@function("pot_total(pot)", "Chips in the pot this hand (every player's committed chips).", min_args=1, max_args=1)
def _pot_total_function(call: Call) -> int:
    _, config = _function_table(call)
    return sum(_p(p, "committed") for p in call.scope.world.entities_of(config.players))


@function("pot_table(pot, viewer)", "Lines describing the table (pot, bets, stacks, who is to act) for the table view.",
          min_args=2, max_args=2)
def _pot_table_function(call: Call) -> List[str]:
    name, config = _function_table(call)
    world: Any = call.scope.world
    seats = _seats(world, config, name)
    street = world.props[f"{name}_street"]
    lines = [f"Hand {world.props[f'{name}_hands']}{' · ' + street if street else ''} · pot "
             f"{sum(_p(p, 'committed') for p in seats)} · highest bet {world.props[f'{name}_current_bet']}"]
    for player in seats:
        status = [] if _p(player, "in_hand") else ["out"]
        if _p(player, "folded"):
            status.append("folded")
        elif _p(player, "in_hand") and _p(player, "stack") == 0:
            status.append("all-in")
        if player.id == world.props[f"{name}_button"]:
            status.append("button")
        if player.id == world.props[f"{name}_to_act"]:
            status.append("to act")
        lines.append(f"{player.name}: stack {_p(player, 'stack')}, bet {_p(player, 'bet')}" + (f" ({', '.join(status)})" if status else ""))
    viewer = world.entity(call.arg(1))
    if viewer is not None and viewer in seats and world.props[f"{name}_to_act"] == viewer.id:
        opts = options(world, config, name, viewer)
        choice = f"to call {opts['call_amount']}" if opts["to_call"] else "nothing to call"
        if opts["can_raise"]:
            choice += f"; raise to {opts['min_raise_to']}–{opts['max_to']}"
        elif opts["can_bet"]:
            choice += f"; bet {opts['min_bet']}–{_p(viewer, 'stack')}"
        lines.append(f"You: {choice}.")
    return lines


# ---------------------------------------------------------------------------
# The pot mechanism
# ---------------------------------------------------------------------------


def _actions(name: str, config: PotConfig) -> Dict[str, Any]:
    opts = f"$pot_options('{name}', $actor)"
    turn = {"expr": f"$world.{name}_to_act == $actor.id", "why": "It is not your turn to bet."}
    all_in = "{$' (all-in)' if $actor.stack == 0 else ''}"

    def act(description: str, rule: str, why: str, move: str, announce: str, outcome: str,
            params: Optional[Dict[str, Any]] = None, amount: Optional[str] = None) -> Dict[str, Any]:
        do: Dict[str, Any] = {"wager": name, "action": move}
        if amount:
            do["amount"] = amount
        return {"by": config.players, "description": description, "params": params or {},
                "when": [turn, {"expr": f"{opts}.{rule}", "why": why}], "do": [do],
                "announce": announce, "outcome": outcome, "terminal": True}

    pot = f"The pot is {{$pot_total('{name}')}}."
    return {
        "fold": act("Give up this hand and the chips you have put in.", "to_call > 0",
                    "Nothing to call: check instead of folding.", "fold", "{$actor.name} folds.", "You folded."),
        "check": act("Stay in without adding chips (nothing to call).", "can_check",
                     "There is a bet to match: call, raise or fold.", "check", "{$actor.name} checks.", "You checked."),
        "call": act("Match the highest bet (all-in if you have fewer chips).", "can_call", "Nothing to call: check instead.",
                    "call", f"{{$actor.name}} calls {{$world.{name}_last.amount}}{all_in}.",
                    f"You called {{$world.{name}_last.amount}}{all_in}. {pot}"),
        "bet": act("Open the betting: `amount` chips, at least the minimum bet (or all you have).", "can_bet",
                   "You cannot bet now (there is a bet already, or nobody left to bet against).", "bet",
                   f"{{$actor.name}} bets {{$params.amount}}{all_in}.", f"You bet {{$params.amount}}{all_in}. {pot}",
                   {"amount": {"type": "int", "min": f"{opts}.min_bet", "max": "$actor.stack", "description": "Chips to bet."}},
                   "$params.amount"),
        "raise": act("Raise: `to` is your new TOTAL bet for this betting round — at least the highest bet plus the last "
                     "raise size, at most everything you have.", "can_raise",
                     "You cannot raise now: nobody has made a full raise since you acted, or you lack the chips.", "raise",
                     f"{{$actor.name}} raises to {{$params.to}}{all_in}.", f"You raised to {{$params.to}}{all_in}. {pot}",
                     {"to": {"type": "int", "min": f"{opts}.min_raise_to", "max": f"{opts}.max_to",
                             "description": "Your total bet after raising."}}, "$params.to"),
        "all_in": act("Put all your chips in.", "can_all_in", "Going all-in would not be a legal raise now: call or fold.",
                      "all_in", "{$actor.name} goes all-in (bet {$actor.bet}).", f"You are all-in. {pot}"),
    }


@mechanism("pot", PotConfig,
           "Poker-style betting: every round is one hand. Generates player chips (stack, bet, committed, folded, "
           "in_hand), fold/check/call/bet/raise/all_in tools with legal amounts in their schemas, one sequential "
           "stage per street that wakes exactly the player to act, blinds and antes, side pots and a showdown paying "
           "each pot to its best `score`. State: $world.<name>_to_act, _current_bet, _min_raise, _button, _result. "
           "Functions: $pot_options, $pot_live, $pot_total. An `end` condition about stacks must also require "
           "$pot_total(<name>) == 0, or it fires while the chips of an all-in hand are still in the pot.",
           example={"kind": "pot", "players": "player", "stack": 500, "blinds": [5, 10], "seat": "$it.seat",
                    "streets": {"preflop": [], "flop": [{"deal": "cards", "count": 3, "zone": "board"}]},
                    "setup": [{"collect": "cards"}, {"deal": "cards", "count": 2, "to": "$filter(player, $it.in_hand)"}],
                    "score": "$poker_rank($hand($it) + $zone(board)).score"})
def _expand_pot(name: str, config: PotConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    if config.players not in types:
        raise MechanismError(f"players '{config.players}' is not a declared type", f"types: {', '.join(types) or 'none'}", "players")
    if config.blinds is not None and len(config.blinds) != 2:
        raise MechanismError("blinds are [small, big]", "e.g. [5, 10]", "blinds")
    if not config.streets:
        raise MechanismError("at least one street (betting round) is needed", '{"betting": []}', "streets")
    live = f"$len($pot_live('{name}')) > 1"
    players = config.players
    stages = []
    for index, (street, effects) in enumerate(config.streets.items()):
        opening = [] if index == 0 and config.blinds else [{"open_betting": name}]
        stages.append({
            "name": street, "when": live, "turns": "sequential", "actions": list(ACTIONS),
            "who": f"$it.id == $world.{name}_to_act", "until": f"$world.{name}_to_act == ''", "passes": 1000,
            "max_actions": 1, "max_calls": config.max_calls, "must_act": True,
            "on_idle": [{"wager": name, "action": "timeout"}], "on_enter": list(effects) + opening,
            "brief": f"{street.replace('_', ' ').capitalize()} betting. The pot is {{$pot_total('{name}')}}; "
                     f"you need {{$pot_options('{name}', $actor).call_amount}} more chips to call.",
        })
    start = [{"new_hand": name}, *config.setup]
    if config.blinds or config.ante not in (0, "0"):
        start.append({"post_blinds": name})
    showdown_effects: List[Any] = [{"if": live, "then": list(config.before_showdown)}] if config.before_showdown else []
    world_props = {
        f"{name}_to_act": {"type": "text", "default": "", "description": "Id of the player to act."},
        f"{name}_current_bet": {"type": "int", "default": 0, "description": "Highest bet this betting round."},
        f"{name}_min_raise": {"type": "int", "default": 0, "description": "Smallest full raise size."},
        f"{name}_button": {"type": "text", "default": ""},
        f"{name}_hands": {"type": "int", "default": 0},
        f"{name}_raises": {"type": "int", "default": 0},
        f"{name}_street": {"type": "text", "default": ""},
        f"{name}_result": {"type": "map", "default": {}, "description": "Last showdown: {hand, pots, payouts, uncontested}."},
        f"{name}_last": {"type": "map", "default": {}, "description": "Last wager: {player, move, amount, to}."},
    }
    fragment: Dict[str, Any] = {
        "types": {players: {"props": {
            "stack": {"type": "int", "default": config.stack, "min": 0, "description": "Chips behind."},
            "bet": {"type": "int", "default": 0, "min": 0, "description": "Chips bet in this betting round."},
            "committed": {"type": "int", "default": 0, "min": 0, "description": "Chips put in the pot this hand."},
            "in_hand": {"type": "bool", "default": True},
            "folded": {"type": "bool", "default": False},
            "acted": {"type": "bool", "default": False},
        }}},
        "world": world_props,
        "actions": _actions(name, config),
        "stages": stages,
        "events": [{"name": f"{name}_hand", "phase": "start", "do": start},
                   {"name": f"{name}_showdown", "phase": "end", "do": showdown_effects + [{"showdown": name}]}],
    }
    if config.conserve:
        world_props[f"{name}_chips"] = {"type": "int", "default": f"$sum({players}, $it.stack)",
                                        "description": "Chips at the table (never changes)."}
        fragment["invariants"] = [{"expr": f"$sum({players}, $it.stack + $it.committed) == $world.{name}_chips",
                                   "why": "Chips are never created or destroyed."}]
    if config.views:
        fragment["views"] = {f"{name}_table": {"for": players, "title": "Table", "of": f"$pot_table('{name}', $actor)",
                                               "show": "{$it}", "bullet": False}}
    return fragment
