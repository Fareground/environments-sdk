"""The pure board engine: positions, legal moves, making moves, attacks, scoring and rendering.

Nothing here touches the world. A :class:`Pos` is a plain value built from the world's piece
entities; :func:`make` returns a new one, and the world bridge writes the difference back through
the journaled world API.
"""
from __future__ import annotations

import hashlib
from collections.abc import Collection
from dataclasses import dataclass

from .board_rules import CaptureRule, Castle, Pattern, Rules

__all__ = ["Pos", "Move", "Played", "legal", "make", "in_check", "has_line", "score", "position_key", "render"]


class Pos:
    """A position: per-piece slots (removed pieces keep their slot with ``at == -1``) plus turn state."""

    __slots__ = ("cells", "ids", "owner", "kind", "at", "moved", "turn", "chain", "ep_cell", "ep_piece", "ko", "hand")

    def __init__(self, size: int, sides: int):
        self.cells: list[int] = [-1] * size
        self.ids: list[str | None] = []
        self.owner: list[int] = []
        self.kind: list[str] = []
        self.at: list[int] = []
        self.moved: list[bool] = []
        self.turn = 0
        #: The piece that must keep capturing, or -1.
        self.chain = -1
        #: The cell a passable step passed over, and the piece that made it (-1 when none).
        self.ep_cell = -1
        self.ep_piece = -1
        #: The cell the side to move may not play on (simple ko), or -1.
        self.ko = -1
        self.hand: list[dict[str, int]] = [{} for _ in range(sides)]

    def add(self, owner: int, kind: str, cell: int, moved: bool = False, piece_id: str | None = None) -> int:
        slot = len(self.at)
        self.ids.append(piece_id)
        self.owner.append(owner)
        self.kind.append(kind)
        self.at.append(cell)
        self.moved.append(moved)
        self.cells[cell] = slot
        return slot

    def copy(self) -> Pos:
        new = Pos.__new__(Pos)
        new.cells, new.ids, new.owner, new.kind = self.cells[:], self.ids[:], self.owner[:], self.kind[:]
        new.at, new.moved, new.hand = self.at[:], self.moved[:], [dict(h) for h in self.hand]
        new.turn, new.chain, new.ko = self.turn, self.chain, self.ko
        new.ep_cell, new.ep_piece = self.ep_cell, self.ep_piece
        return new

    def pieces(self, side: int) -> list[int]:
        return [p for p in range(len(self.at)) if self.owner[p] == side and self.at[p] >= 0]

    def lift(self, slot: int) -> None:
        if self.at[slot] >= 0:
            self.cells[self.at[slot]] = -1
            self.at[slot] = -1


@dataclass(frozen=True)
class Move:
    text: str
    piece: int = -1
    frm: int = -1
    to: int = -1
    captures: tuple[int, ...] = ()
    promote: str | None = None
    place: str | None = None
    rook: int = -1
    rook_from: int = -1
    rook_to: int = -1
    chain: bool = False
    passed: int = -1


@dataclass
class Played:
    pos: Pos
    piece: int
    captured: int
    flipped: int
    suicide: bool
    continues: bool


# ---------------------------------------------------------------------------
# Move generation
# ---------------------------------------------------------------------------


def legal(rules: Rules, pos: Pos, side: int) -> list[Move]:
    """Every legal move for ``side`` in ``pos`` (chains, ko and en passant apply to the side to move)."""
    moves: list[Move] = []
    if pos.chain >= 0 and side == pos.turn:
        _piece_moves(rules, pos, pos.chain, side, moves)
        moves = [m for m in moves if m.chain and m.captures]
    else:
        for slot in pos.pieces(side):
            _piece_moves(rules, pos, slot, side, moves)
        _castles(rules, pos, side, moves)
        _placements(rules, pos, side, moves)
    if rules.config.mandatory_capture:
        captures = [m for m in moves if m.captures]
        if captures:
            moves = captures
    moves = _filtered(rules, pos, side, moves)
    seen: dict[str, Move] = {}
    for move in moves:
        seen.setdefault(move.text, move)
    return list(seen.values())


def _filtered(rules: Rules, pos: Pos, side: int, moves: list[Move]) -> list[Move]:
    check = rules.config.self_check and bool(rules.royal)
    flip, enclose = rules.flip_rule(), rules.enclose_rule()
    need_flip = flip is not None and flip.required
    no_suicide = enclose is not None and not enclose.suicide
    if not (check or need_flip or no_suicide):
        return moves
    out = []
    for move in moves:
        played = make(rules, pos, move, side, continue_chain=False)
        if need_flip and move.place is not None and played.flipped == 0:
            continue
        if no_suicide and played.suicide:
            continue
        if check and in_check(rules, played.pos, side):
            continue
        out.append(move)
    return out


def _piece_moves(rules: Rules, pos: Pos, slot: int, side: int, out: list[Move]) -> None:
    at, kind = pos.at[slot], pos.kind[slot]
    for pat in rules.patterns[side][kind]:
        if pat.first and pos.moved[slot]:
            continue
        if pat.origin is not None and at not in pat.origin:
            continue
        if pat.mode == "jump":
            _jumps(rules, pos, slot, side, pat, out)
            continue
        for target, passed in _reach(pos, at, pat):
            occupant = pos.cells[target]
            if occupant < 0:
                if pat.only != "capture":
                    _emit(rules, pos, slot, side, target, (), pat, out, passed if pat.passable else -1)
                elif pat.en_passant and target == pos.ep_cell and side == pos.turn and pos.ep_piece >= 0 \
                        and pos.owner[pos.ep_piece] != side and pos.at[pos.ep_piece] >= 0:
                    _emit(rules, pos, slot, side, target, (pos.ep_piece,), pat, out)
            elif pos.owner[occupant] != side and pat.only != "move":
                _emit(rules, pos, slot, side, target, (occupant,), pat, out)


def _reach(pos: Pos, at: int, pat: Pattern) -> list[tuple[int, int]]:
    """Cells a non-jump pattern can land on (occupied landing cells included), with the cell passed last."""
    out: list[tuple[int, int]] = []
    within = pat.within
    if pat.mode == "leap":
        for table in pat.tables:
            target = table[at]
            if target >= 0 and (within is None or target in within):
                out.append((target, -1))
    elif pat.mode == "step":
        for table in pat.tables:
            cell, previous = at, -1
            for k in range(pat.distance):
                previous, cell = cell, table[cell]
                if cell < 0 or (k < pat.distance - 1 and pos.cells[cell] >= 0):
                    cell = -1
                    break
            if cell >= 0 and cell != at and (within is None or cell in within):
                out.append((cell, previous if pat.distance > 1 else -1))
    else:
        for table in pat.tables:
            cell, count = at, 0
            while True:
                cell = table[cell]
                count += 1
                if cell < 0 or cell == at or (pat.max is not None and count > pat.max):
                    break
                if within is None or cell in within:
                    out.append((cell, -1))
                if pos.cells[cell] >= 0:
                    break
    return out


def _jumps(rules: Rules, pos: Pos, slot: int, side: int, pat: Pattern, out: list[Move]) -> None:
    at = pos.at[slot]
    for table in pat.tables:
        middle = table[at]
        if middle < 0:
            continue
        jumped = pos.cells[middle]
        if jumped < 0:
            continue
        own = pos.owner[jumped] == side
        if (pat.over == "enemy" and own) or (pat.over == "own" and not own):
            continue
        land = table[middle]
        if land < 0 or pos.cells[land] >= 0 or (pat.within is not None and land not in pat.within):
            continue
        _emit(rules, pos, slot, side, land, (jumped,) if pat.capture and not own else (), pat, out)


def _emit(rules: Rules, pos: Pos, slot: int, side: int, target: int, captures: tuple[int, ...], pat: Pattern,
          out: list[Move], passed: int = -1) -> None:
    names = rules.geo.names
    frm = pos.at[slot]
    text = f"{names[frm]}{'x' if captures else '-'}{names[target]}"
    promotion = rules.promotions[side].get(pos.kind[slot])
    if promotion is not None and target in promotion.zone:
        choice = len(promotion.to) > 1 or promotion.optional
        for kind in promotion.to:
            if kind != pos.kind[slot]:
                out.append(Move(f"{text}={kind}" if choice else text, slot, frm, target, captures, kind,
                                chain=pat.chain, passed=passed))
        if not promotion.optional:
            return
    out.append(Move(text, slot, frm, target, captures, chain=pat.chain, passed=passed))


def _castles(rules: Rules, pos: Pos, side: int, out: list[Move]) -> None:
    for castle in rules.castles:
        if castle.side != side:
            continue
        king, rook = pos.cells[castle.king], pos.cells[castle.rook]
        if king < 0 or rook < 0 or pos.owner[king] != side or pos.owner[rook] != side:
            continue
        if pos.kind[king] not in rules.royal or pos.moved[king] or pos.moved[rook]:
            continue
        if any(pos.cells[cell] >= 0 for cell in castle.empty):
            continue
        if rules.config.self_check and any(attacked(rules, pos, castle.safe, other)
                                           for other in range(len(rules.sides)) if other != side):
            continue
        out.append(Move(castle.text, king, castle.king, castle.king_to, rook=rook, rook_from=castle.rook,
                        rook_to=castle.rook_to))


def _placements(rules: Rules, pos: Pos, side: int, out: list[Move]) -> None:
    if not rules.place_kinds:
        return
    if rules.place_from == "hand":
        kinds = [k for k in rules.place_kinds if pos.hand[side].get(k, 0) > 0]
    else:
        kinds = list(rules.place_kinds)
    if not kinds:
        return
    zone = rules.place_zone[side]
    gravity = rules.gravity
    names = rules.geo.names
    single = len(rules.place_kinds) == 1
    for cell in range(len(pos.cells)):
        if pos.cells[cell] >= 0 or (zone is not None and cell not in zone):
            continue
        if gravity is not None and gravity[cell] >= 0 and pos.cells[gravity[cell]] < 0:
            continue
        if cell == pos.ko and side == pos.turn:
            continue
        for kind in kinds:
            out.append(Move(names[cell] if single else f"{kind}@{names[cell]}", to=cell, place=kind))


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------


def attacked(rules: Rules, pos: Pos, cells: Collection[int], by: int) -> bool:
    """True when a piece of side ``by`` could capture on any of ``cells`` (occupied or not)."""
    for slot in range(len(pos.at)):
        at = pos.at[slot]
        if at < 0 or pos.owner[slot] != by:
            continue
        for pat in rules.patterns[by][pos.kind[slot]]:
            if (pat.only == "move" or (pat.first and pos.moved[slot])
                or (pat.origin is not None and at not in pat.origin)):
                continue
            if pat.mode == "jump":
                if not pat.capture:
                    continue
                for table in pat.tables:
                    middle = table[at]
                    if middle in cells and middle >= 0:
                        land = table[middle]
                        if land >= 0 and pos.cells[land] < 0 and (pat.within is None or land in pat.within):
                            return True
                continue
            for target, _ in _reach(pos, at, pat):
                if target in cells:
                    return True
    return False


def in_check(rules: Rules, pos: Pos, side: int) -> bool:
    """True when one of ``side``'s royal pieces is attacked by another side."""
    royals = [pos.at[p] for p in pos.pieces(side) if pos.kind[p] in rules.royal]
    if not royals:
        return False
    return any(attacked(rules, pos, royals, other) for other in range(len(rules.sides)) if other != side)


# ---------------------------------------------------------------------------
# Making moves
# ---------------------------------------------------------------------------


def make(rules: Rules, pos: Pos, move: Move, side: int, continue_chain: bool = True) -> Played:
    """The position after ``side`` plays ``move``: captures, promotion, capture rules, chains and the turn."""
    new = pos.copy()
    captured = 0
    for slot in move.captures:
        new.lift(slot)
        captured += 1
    if move.place is not None:
        piece = new.add(side, move.place, move.to, True)
        if rules.place_from == "hand":
            new.hand[side][move.place] = new.hand[side].get(move.place, 0) - 1
    else:
        piece = move.piece
        new.cells[move.frm] = -1
        if move.rook >= 0:
            new.cells[move.rook_from] = -1
            new.cells[move.rook_to] = move.rook
            new.at[move.rook], new.moved[move.rook] = move.rook_to, True
        new.cells[move.to] = piece
        new.at[piece], new.moved[piece] = move.to, True
        if move.promote is not None:
            new.kind[piece] = move.promote
    flipped, suicide = 0, False
    new.ko = -1
    for rule in rules.captures:
        if rule.rule == "custodial":
            captured += _custodial(new, move.to, side, rule)
        elif rule.rule == "flip":
            flipped += _flip(new, move.to, side, rule)
        else:
            taken, suicide = _enclose(rules, new, move.to, side, rule)
            captured += taken
    new.ep_cell, new.ep_piece = (move.passed, piece) if move.passed >= 0 else (-1, -1)
    continues = False
    if continue_chain and move.chain and move.captures and new.at[piece] >= 0:
        promotion = rules.promotions[side].get(pos.kind[piece]) if move.place is None else None
        if not (move.promote is not None and promotion is not None and promotion.ends_turn):
            follow: list[Move] = []
            _piece_moves(rules, new, piece, side, follow)
            continues = any(m.chain and m.captures for m in follow)
    new.chain = piece if continues else -1
    new.turn = side if continues else (side + 1) % len(rules.sides)
    return Played(new, piece, captured, flipped, suicide, continues)


def _custodial(pos: Pos, cell: int, side: int, rule: CaptureRule) -> int:
    taken = 0
    for table in rule.tables:
        near = table[cell]
        if near < 0:
            continue
        victim = pos.cells[near]
        if victim < 0 or pos.owner[victim] == side or pos.kind[victim] in rule.immune:
            continue
        far = table[near]
        if far < 0:
            continue
        closer = pos.cells[far]
        if (closer >= 0 and pos.owner[closer] == side) or (closer < 0 and far in rule.hostile):
            pos.lift(victim)
            taken += 1
    return taken


def _flip(pos: Pos, cell: int, side: int, rule: CaptureRule) -> int:
    flipped = 0
    for table in rule.tables:
        line: list[int] = []
        current = table[cell]
        while current >= 0 and pos.cells[current] >= 0 and pos.owner[pos.cells[current]] != side:
            line.append(pos.cells[current])
            current = table[current]
        if line and current >= 0 and pos.cells[current] >= 0 and pos.owner[pos.cells[current]] == side:
            for slot in line:
                pos.owner[slot] = side
            flipped += len(line)
    return flipped


def _group(rules: Rules, pos: Pos, cell: int) -> tuple[list[int], int]:
    """The connected pieces of one side containing ``cell`` and their number of liberties."""
    owner = pos.owner[pos.cells[cell]]
    adjacent = rules.geo.adjacent
    stones, liberties, seen, stack = [], set(), {cell}, [cell]
    while stack:
        current = stack.pop()
        stones.append(current)
        for nxt in adjacent[current]:
            occupant = pos.cells[nxt]
            if occupant < 0:
                liberties.add(nxt)
            elif nxt not in seen and pos.owner[occupant] == owner:
                seen.add(nxt)
                stack.append(nxt)
    return stones, len(liberties)


def _enclose(rules: Rules, pos: Pos, cell: int, side: int, rule: CaptureRule) -> tuple[int, bool]:
    taken: list[int] = []
    checked: set = set()
    for nxt in rules.geo.adjacent[cell]:
        occupant = pos.cells[nxt]
        if occupant < 0 or pos.owner[occupant] == side or nxt in checked:
            continue
        stones, liberties = _group(rules, pos, nxt)
        checked.update(stones)
        if liberties == 0:
            taken.extend(stones)
    for stone in taken:
        pos.lift(pos.cells[stone])
    stones, liberties = _group(rules, pos, cell)
    suicide = liberties == 0
    if suicide:
        for stone in stones:
            pos.lift(pos.cells[stone])
    elif rule.ko and len(taken) == 1 and len(stones) == 1 and liberties == 1:
        pos.ko = taken[0]
    return len(taken) + (len(stones) if suicide else 0), suicide


# ---------------------------------------------------------------------------
# Judging and scoring
# ---------------------------------------------------------------------------


def has_line(rules: Rules, pos: Pos, side: int, length: int) -> bool:
    """True when ``side`` has ``length`` pieces in a row (or fills one of the declared lines)."""
    if rules.lines:
        return any(all(pos.cells[c] >= 0 and pos.owner[pos.cells[c]] == side for c in line) for line in rules.lines)
    geo = rules.geo
    for direction in geo.line_dirs:
        table = geo.step[direction]
        for slot in pos.pieces(side):
            start = pos.at[slot]
            run, cell = 1, table[start]
            while run < length and cell >= 0 and cell != start and pos.cells[cell] >= 0 \
                    and pos.owner[pos.cells[cell]] == side:
                run += 1
                cell = table[cell]
            if run >= length:
                return True
    return False


def score(rules: Rules, pos: Pos) -> list[float]:
    """Each side's score: pieces on the board, plus surrounded empty regions for area scoring, plus komi."""
    totals = [float(len(pos.pieces(side))) for side in range(len(rules.sides))]
    if rules.config.score == "area":
        adjacent, seen = rules.geo.adjacent, set()
        for start in range(len(pos.cells)):
            if pos.cells[start] >= 0 or start in seen:
                continue
            region, borders, stack = 0, set(), [start]
            seen.add(start)
            while stack:
                cell = stack.pop()
                region += 1
                for nxt in adjacent[cell]:
                    occupant = pos.cells[nxt]
                    if occupant >= 0:
                        borders.add(pos.owner[occupant])
                    elif nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            if len(borders) == 1:
                totals[borders.pop()] += region
    for side, extra in rules.config.komi.items():
        totals[rules.sides.index(side)] += extra
    return totals


def position_key(rules: Rules, pos: Pos, moves: list[Move]) -> str:
    """A short fingerprint of what repetition compares: pieces, side to move, castling and en passant
    rights (only when a capture there is actually possible), ko and hands."""
    symbols = rules.symbols
    board = "".join(symbols[(pos.owner[s], pos.kind[s])] if s >= 0 else "." for s in pos.cells)
    rights = "".join("1" if _castle_right(rules, pos, c) else "0" for c in rules.castles)
    ep = pos.ep_cell if any(pos.ep_piece in m.captures and m.to == pos.ep_cell for m in moves) else -1
    hands = ";".join(",".join(f"{k}{n}" for k, n in sorted(h.items()) if n) for h in pos.hand)
    text = f"{board}|{pos.turn}|{pos.chain}|{rights}|{ep}|{pos.ko}|{hands}"
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _castle_right(rules: Rules, pos: Pos, castle: Castle) -> bool:
    king, rook = pos.cells[castle.king], pos.cells[castle.rook]
    return king >= 0 and rook >= 0 and not pos.moved[king] and not pos.moved[rook]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(rules: Rules, pos: Pos) -> list[str]:
    """The board as compact text lines with coordinates."""
    geo = rules.geo
    marks = [rules.symbols[(pos.owner[s], pos.kind[s])] if s >= 0 else "." for s in pos.cells]
    if geo.shape == "grid":
        algebraic = rules.config.coords == "algebraic"
        width = 1 if algebraic else len(str(geo.cols))
        label = len(str(geo.rows))
        lines = []
        for r in range(geo.rows):
            number = geo.rows - r if algebraic else r + 1
            row = " ".join(marks[r * geo.cols + c].ljust(width) for c in range(geo.cols)).rstrip()
            lines.append(f"{str(number).rjust(label)} {row}")
        heads = [chr(ord("a") + c) if algebraic else str(c + 1) for c in range(geo.cols)]
        lines.append(" " * (label + 1) + " ".join(h.ljust(width) for h in heads).rstrip())
        return lines
    if geo.shape == "hex":
        rows: dict[int, list[int]] = {}
        for cell, (_, r) in enumerate(geo.coords):
            rows.setdefault(r, []).append(cell)
        lines = []
        for r in sorted(rows):
            cells = rows[r]
            indent = " " * (geo.coords[cells[0]][0] * 2 + r - min(q * 2 + rr for q, rr in geo.coords))
            lines.append(f"{str(r + 1).rjust(2)} {indent}{' '.join(marks[c] for c in cells)}   (from "
                         f"{geo.names[cells[0]]})")
        return lines
    if geo.shape == "ring":
        occupied = [f"{geo.names[c]}={marks[c]}" for c in range(geo.size) if pos.cells[c] >= 0]
        kind = "ring" if geo.wrap else "track"
        return [f"{kind} of {geo.size} cells: {', '.join(occupied) or 'empty'}"]
    return [", ".join(f"{geo.names[c]}={marks[c]}" for c in range(geo.size))]
