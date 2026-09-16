"""The board-game grammar: the ``game.board`` mode's config, and its compilation into fast rule tables.

Config is validated by pydantic (field errors name the field); compilation checks every cross
reference — directions, cells, zones, kinds, sides — and raises :class:`MechanismError` with a fix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import permutations, product
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ..registry import MechanismError
from ._common import ToolsSetting, tools_field
from .board_geometry import Geometry, GeometryError, graph, grid, hex_board, ring

__all__ = ["BoardConfig", "Rules", "Pattern", "Promotion", "Castle", "CaptureRule", "compile_rules"]

Dirs = Union[str, List[str]]
Zone = Union[str, List[str], Dict[str, Union[str, List[str]]]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MoveSpec(_Strict):
    """One movement pattern of a piece kind. Exactly one of step, slide, leap, jump."""

    step: Optional[Dirs] = Field(None, description="Move `distance` cells in these directions through empty cells (\"adjacent\": to any neighbouring cell, e.g. on graphs).")
    slide: Optional[Dirs] = Field(None, description="Move any number of cells along these directions until blocked.")
    leap: Optional[List[Any]] = Field(None, description="Jump to an offset [forward, right] (or a list of them), ignoring what is between; every mirror and rotation unless `exact`.")
    jump: Optional[Dirs] = Field(None, description="Hop over the adjacent piece in these directions onto the empty cell beyond.")
    distance: int = Field(1, ge=1, description="step: exact number of cells (cells passed over must be empty).")
    max: Optional[int] = Field(None, ge=1, description="slide: at most this many cells.")
    only: Optional[Literal["move", "capture"]] = Field(None, description="move: only onto empty cells; capture: only onto enemies.")
    first: bool = Field(False, description="Only for a piece that has not moved yet.")
    from_: Optional[Zone] = Field(None, alias="from", description="Only from cells in this zone.")
    within: Optional[Zone] = Field(None, description="Only onto cells in this zone.")
    over: Literal["enemy", "own", "any"] = Field("enemy", description="jump: which pieces may be jumped.")
    capture: Optional[bool] = Field(None, description="jump: remove the jumped piece (default: when jumping enemies).")
    chain: bool = Field(False, description="After a capture with this pattern the same piece keeps capturing while it can (multi-jump).")
    passable: bool = Field(False, description="A multi-cell step that an en_passant pattern can capture on the cell passed over.")
    en_passant: bool = Field(False, description="May also capture a piece that just made a `passable` step over the target cell.")
    exact: bool = Field(False, description="leap: use the offsets as given, relative to the side's forward direction.")


class PromoteSpec(_Strict):
    """Promotion: a piece ending a move in `zone` becomes one of `to`."""

    zone: Zone = Field("far", description="Where promotion happens (default: the far edge for the side).")
    to: List[str] = Field(..., min_length=1, description="Piece kinds it may become; several = the mover chooses (e7-e8=Q).")
    optional: bool = Field(False, description="The mover may also decline promotion.")
    ends_turn: bool = Field(True, description="Promotion ends a capture chain.")


class PieceSpec(_Strict):
    """A piece kind."""

    name: Optional[str] = Field(None, description="Readable name (default: the kind key).")
    symbol: Optional[Union[str, Dict[str, str]]] = Field(None, description="One-character board symbol, or {side: symbol}.")
    moves: List[MoveSpec] = Field(default_factory=list)
    royal: bool = Field(False, description="Losing it loses the game; with self_check it may not be left attacked.")
    promote: Optional[PromoteSpec] = None
    irreversible: bool = Field(False, description="Its moves reset the move_limit and repetition count (pawns).")


class SideSpec(_Strict):
    """A seat at the board: a player entity id."""

    id: str
    name: Optional[str] = None
    forward: Optional[str] = Field(None, description="Direction this side's pieces advance (grid default: n for the first side, s for others).")
    mark: Optional[str] = Field(None, description="Board symbol when there is a single piece kind (default: first letter of the id).")


class PlaceSpec(_Strict):
    """Placing new pieces on empty cells (Go, Othello, Connect Four, drops)."""

    kinds: Optional[List[str]] = Field(None, description="Kinds that can be placed (default: every kind).")
    from_: Literal["supply", "hand"] = Field("supply", alias="from", description="supply: unlimited; hand: counts in `hand`.")
    where: Optional[Zone] = Field(None, description="Only on cells in this zone.")
    gravity: Optional[str] = Field(None, description="A placed piece falls this way to the last empty cell (\"s\" for Connect Four).")


class CaptureSpec(_Strict):
    """A capture rule applied after every move or placement."""

    rule: Literal["custodial", "flip", "enclose"] = Field(..., description="custodial: sandwich an enemy between the moved piece and a friend; flip: turn over enemy lines closed by a friend (Othello); enclose: remove groups without liberties (Go).")
    dirs: Optional[Dirs] = Field(None, description="custodial default orthogonal; flip default all.")
    hostile: Optional[Zone] = Field(None, description="custodial: empty cells that also close a sandwich (throne, corners).")
    immune: List[str] = Field(default_factory=list, description="custodial: kinds that cannot be sandwiched.")
    required: bool = Field(False, description="flip: a placement must flip at least one piece.")
    suicide: bool = Field(False, description="enclose: a move may leave its own group without liberties (removing it).")
    ko: bool = Field(True, description="enclose: simple ko — a single stone cannot be retaken at once.")


class CastleSpec(_Strict):
    """A castling move: royal `king` and `rook` both unmoved, cells between empty, the king not in check nor passing attacked cells."""

    side: str
    king: str
    rook: str
    king_to: str
    rook_to: str
    name: Optional[str] = Field(None, description="Notation (default: the king's move, e.g. e1-g1).")


class BoardConfig(_Strict):
    """An abstract board game between seated players."""

    shape: Literal["grid", "hex", "ring", "graph"] = Field("grid", description="grid | hex | ring | graph.")
    size: Union[int, List[int]] = Field(8, description="grid: n or [rows, cols]; hex: radius or [rows, cols]; ring: cells.")
    coords: Literal["algebraic", "rc"] = Field("algebraic", description="grid cell names: algebraic (a1 bottom-left) or rc (row,col from the top-left).")
    wrap: bool = Field(True, description="ring: the last cell joins the first (false = a track).")
    nodes: List[str] = Field(default_factory=list, description="graph: place names.")
    edges: List[List[str]] = Field(default_factory=list, description="graph: [a, b] or [a, b, dir, back_dir].")
    cells: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Cell properties: {cell: {color: dark, terrain: …}}; zones can select them (\"color=dark\").")
    zones: Dict[str, Zone] = Field(default_factory=dict, description="Named cell sets: a list, \"rank 8\", \"file a\", \"far\", \"near\", \"prop=value\", another zone, or {side: zone}.")
    sides: List[Union[str, SideSpec]] = Field(..., min_length=1, description="Player entity ids in seat order; the first moves first.")
    who: str = Field("player", description="Agent type of the players (declared for you when missing).")
    piece_type: str = Field("piece", description="Entity type of the pieces (props owner, kind, cell, moved).")
    pieces: Dict[str, PieceSpec] = Field(..., min_length=1, description="Piece kinds: {kind: {name, symbol, moves, royal, promote, irreversible}}.")
    setup: Union[str, Dict[str, Dict[str, List[str]]]] = Field("", description="Start position: grid rows top to bottom as board symbols (\"rnbqkbnr/pppppppp/8/…\"), or {side: {kind: [cells]}}.")
    hand: Dict[str, Dict[str, int]] = Field(default_factory=dict, description="Pieces in reserve at the start: {side: {kind: count}} (with place.from hand).")
    place: Optional[PlaceSpec] = Field(None, description="Players may place pieces on empty cells: {kinds, from, where, gravity}.")
    captures: List[CaptureSpec] = Field(default_factory=list, description="Capture rules after each move: custodial | flip | enclose.")
    castling: List[CastleSpec] = Field(default_factory=list, description="Castling moves: [{side, king, rook, king_to, rook_to, name}].")
    mandatory_capture: bool = Field(False, description="When a capture is available, only captures are legal.")
    self_check: bool = Field(False, description="A move may not leave your royal piece attacked (check, checkmate, stalemate).")
    allow_pass: bool = Field(False, alias="pass", description="Players may pass (a `<name>_pass` tool).")
    no_moves: Literal["lose", "draw", "pass"] = Field("lose", description="When the player to move has no legal move: lose (checkmate), draw, or pass automatically.")
    stalemate: Literal["draw", "lose"] = Field("draw", description="No legal move while the royal piece is not attacked.")
    line: Optional[int] = Field(None, ge=2, description="Win by n of your pieces in a row.")
    lines: List[List[str]] = Field(default_factory=list, description="Explicit winning lines (cells) instead of straight rows.")
    repetition: Optional[int] = Field(None, ge=2, description="Draw when the same position occurs this many times.")
    move_limit: Optional[int] = Field(None, ge=1, description="Draw after this many moves in a row without a capture or irreversible move (100 = the 50-move rule).")
    passes_end: Optional[int] = Field(None, ge=1, description="Consecutive passes that end the game (default: one per side).")
    score: Literal["none", "pieces", "area"] = Field("none", description="Winner when the game ends by passes or when nobody can move: pieces on the board, or area (pieces + surrounded empty cells).")
    komi: Dict[str, float] = Field(default_factory=dict, description="Points added to a side's score: {white: 6.5}.")
    stage: Optional[str] = Field(None, description="Play during this declared stage instead of a generated one.")
    tools: ToolsSetting = tools_field()


# ---------------------------------------------------------------------------
# Compiled rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pattern:
    mode: str  # step | slide | leap | jump
    #: step/slide/jump: one neighbour table per direction; leap: one offset table per offset.
    tables: Tuple[List[int], ...]
    distance: int = 1
    max: Optional[int] = None
    only: Optional[str] = None
    first: bool = False
    origin: Optional[FrozenSet[int]] = None
    within: Optional[FrozenSet[int]] = None
    over: str = "enemy"
    capture: bool = True
    chain: bool = False
    passable: bool = False
    en_passant: bool = False


@dataclass(frozen=True)
class Promotion:
    zone: FrozenSet[int]
    to: Tuple[str, ...]
    optional: bool
    ends_turn: bool


@dataclass(frozen=True)
class Castle:
    side: int
    king: int
    rook: int
    king_to: int
    rook_to: int
    text: str
    #: Cells that must be empty (between king and rook, and both destinations), ignoring the two pieces.
    empty: FrozenSet[int]
    #: Cells the king stands on or crosses, which may not be attacked.
    safe: Tuple[int, ...]


@dataclass(frozen=True)
class CaptureRule:
    rule: str
    tables: Tuple[List[int], ...]
    hostile: FrozenSet[int]
    immune: FrozenSet[str]
    required: bool
    suicide: bool
    ko: bool


@dataclass
class Rules:
    """Everything the engine needs, as index tables."""

    name: str
    config: BoardConfig
    geo: Geometry
    sides: List[str]
    side_names: List[str]
    kinds: List[str]
    kind_names: Dict[str, str]
    royal: FrozenSet[str]
    irreversible: FrozenSet[str]
    #: patterns[side][kind]
    patterns: List[Dict[str, List[Pattern]]]
    #: promotions[side][kind]
    promotions: List[Dict[str, Promotion]]
    symbols: Dict[Tuple[int, str], str]
    by_symbol: Dict[str, Tuple[int, str]]
    place_kinds: Tuple[str, ...] = ()
    place_from: str = "supply"
    place_zone: List[Optional[FrozenSet[int]]] = field(default_factory=list)
    gravity: Optional[List[int]] = None
    captures: Tuple[CaptureRule, ...] = ()
    castles: Tuple[Castle, ...] = ()
    lines: Tuple[Tuple[int, ...], ...] = ()
    chains: bool = False

    def flip_rule(self) -> Optional[CaptureRule]:
        return next((c for c in self.captures if c.rule == "flip"), None)

    def enclose_rule(self) -> Optional[CaptureRule]:
        return next((c for c in self.captures if c.rule == "enclose"), None)


def compile_rules(name: str, config: BoardConfig) -> Rules:
    """Compile a validated config; raises :class:`MechanismError` naming the field to fix."""
    try:
        return _Compiler(name, config).build()
    except GeometryError as exc:
        raise MechanismError(str(exc), "check the cell names and directions against the board shape") from None


class _Compiler:
    def __init__(self, name: str, config: BoardConfig):
        self.name = name
        self.c = config
        self.geo = self._geometry()
        self.sides = [s if isinstance(s, str) else s.id for s in config.sides]
        self.specs = [SideSpec(id=s) if isinstance(s, str) else s for s in config.sides]

    def build(self) -> Rules:
        c, geo = self.c, self.geo
        if len(set(self.sides)) != len(self.sides):
            raise MechanismError("side ids must be distinct", "give every side its own id", "sides")
        forwards = [self._forward(i) for i in range(len(self.sides))]
        kinds = list(c.pieces)
        for kind in kinds:
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", kind):
                raise MechanismError(f"piece kind '{kind}' must start with a letter and use letters, digits and _",
                                     "rename it, e.g. \"P\" or \"stone\"", f"pieces.{kind}")
        patterns: List[Dict[str, List[Pattern]]] = []
        promotions: List[Dict[str, Promotion]] = []
        for side, forward in enumerate(forwards):
            patterns.append({k: [self._pattern(p, side, forward, f"pieces.{k}.moves[{i}]")
                                 for i, p in enumerate(spec.moves)] for k, spec in c.pieces.items()})
            promotions.append({k: self._promotion(spec.promote, side, f"pieces.{k}.promote")
                               for k, spec in c.pieces.items() if spec.promote is not None})
        symbols = self._symbols(kinds)
        rules = Rules(
            name=self.name, config=c, geo=geo, sides=self.sides,
            side_names=[s.name or s.id.replace("_", " ").title() for s in self.specs],
            kinds=kinds, kind_names={k: spec.name or k for k, spec in c.pieces.items()},
            royal=frozenset(k for k, spec in c.pieces.items() if spec.royal),
            irreversible=frozenset(k for k, spec in c.pieces.items() if spec.irreversible),
            patterns=patterns, promotions=promotions, symbols=symbols,
            by_symbol={sym: key for key, sym in symbols.items()},
            captures=tuple(self._capture(rule, f"captures[{i}]") for i, rule in enumerate(c.captures)),
            castles=tuple(self._castle(spec, f"castling[{i}]") for i, spec in enumerate(c.castling)),
            lines=tuple(tuple(geo.cell(cell, f"lines[{i}]") for cell in line) for i, line in enumerate(c.lines)),
        )
        rules.chains = any(p.chain for side in patterns for pats in side.values() for p in pats)
        if c.place is not None:
            self._place(rules, c.place)
        for side_id in list(c.komi) + list(c.hand):
            self._side(side_id, "komi" if side_id in c.komi else "hand")
        for side_id, counts in c.hand.items():
            for kind in counts:
                self._kind(kind, f"hand.{side_id}")
        for cell in c.cells:
            geo.cell(cell, f"cells.{cell}")
        if c.line is None and c.lines:
            raise MechanismError("`lines` lists winning lines but `line` is not set", "set \"line\" to the line length", "line")
        return rules

    # -- pieces ------------------------------------------------------------------

    def _geometry(self) -> Geometry:
        c = self.c
        size = c.size
        if c.shape == "grid":
            rows, cols = (size, size) if isinstance(size, int) else _pair(size, "size")
            return grid(rows, cols, c.coords)
        if c.shape == "hex":
            if isinstance(size, int):
                return hex_board(radius=size)
            rows, cols = _pair(size, "size")
            return hex_board(rows=rows, cols=cols)
        if c.shape == "ring":
            if not isinstance(size, int):
                raise MechanismError("a ring's size is its number of cells", "e.g. \"size\": 24", "size")
            return ring(size, c.wrap)
        if not c.nodes:
            raise MechanismError("a graph board needs `nodes` (and `edges`)", "list the places", "nodes")
        return graph(c.nodes, c.edges)

    def _forward(self, side: int) -> Optional[str]:
        spec, geo = self.specs[side], self.geo
        if spec.forward is not None:
            if spec.forward not in geo.dirs:
                raise MechanismError(f"forward '{spec.forward}' is not a direction on this board",
                                     f"directions: {', '.join(geo.dirs)}", f"sides[{side}].forward")
            return spec.forward
        if geo.shape == "grid":
            return "n" if side == 0 else "s"
        if geo.shape == "hex":
            return "ne" if side == 0 else "sw"
        if geo.shape == "ring":
            return "cw"
        return None

    def _pattern(self, spec: MoveSpec, side: int, forward: Optional[str], where: str) -> Pattern:
        geo = self.geo
        modes = [m for m in ("step", "slide", "leap", "jump") if getattr(spec, m) is not None]
        if len(modes) != 1:
            raise MechanismError("a move pattern names exactly one of step, slide, leap, jump",
                                 "e.g. {\"slide\": \"orthogonal\"}", where)
        mode = modes[0]
        if mode == "leap":
            tables = tuple(geo.offset_table(o) for o in self._offsets(spec, forward, where))
        elif mode == "step" and spec.step == "adjacent":
            if spec.distance != 1:
                raise MechanismError("an \"adjacent\" step moves exactly one cell", "remove `distance`", where)
            degree = max(len(near) for near in geo.adjacent)
            tables = tuple([near[k] if k < len(near) else -1 for near in geo.adjacent] for k in range(degree))
        else:
            dirs = geo.expand(getattr(spec, mode), f"{where}.{mode}", forward)
            tables = tuple(geo.step[d] for d in dirs)
        return Pattern(
            mode=mode, tables=tables, distance=spec.distance, max=spec.max, only=spec.only, first=spec.first,
            origin=self.zone(spec.from_, side, f"{where}.from"), within=self.zone(spec.within, side, f"{where}.within"),
            over=spec.over, capture=spec.capture if spec.capture is not None else spec.over == "enemy",
            chain=spec.chain, passable=spec.passable, en_passant=spec.en_passant)

    def _offsets(self, spec: MoveSpec, forward: Optional[str], where: str) -> List[Tuple[int, int]]:
        raw = spec.leap or []
        offsets = [raw] if raw and all(isinstance(v, int) for v in raw) else raw
        pairs: List[Tuple[int, int]] = []
        for item in offsets:
            if not (isinstance(item, list) and len(item) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in item)):
                raise MechanismError(f"a leap offset is [forward, right] whole numbers, got {item!r}",
                                     "e.g. {\"leap\": [2, 1]} for a knight", f"{where}.leap")
            pairs.append((item[0], item[1]))
        geo = self.geo
        if geo.shape not in ("grid", "hex"):
            raise MechanismError("leaps need a grid or hex board", "use step with a distance on rings and graphs", f"{where}.leap")
        out: List[Tuple[int, int]] = []
        for a, b in pairs:
            if geo.shape == "hex":
                variants = [(a, b)] if spec.exact else _hex_symmetries(a, b)
            elif spec.exact:
                if forward not in ("n", "s", "e", "w"):
                    raise MechanismError("exact leaps need an orthogonal forward direction", "set the side's forward to n, s, e or w",
                                         f"{where}.leap")
                fr, fc = {"n": (-1, 0), "s": (1, 0), "e": (0, 1), "w": (0, -1)}[forward]
                rr, rc = _right(forward)
                variants = [(a * fr + b * rr, a * fc + b * rc)]
            else:
                variants = sorted({(sa * x, sb * y) for x, y in set(permutations((a, b))) for sa, sb in product((1, -1), repeat=2)})
            out.extend(v for v in variants if v not in out and v != (0, 0))
        return out

    def _promotion(self, spec: PromoteSpec, side: int, where: str) -> Promotion:
        for kind in spec.to:
            self._kind(kind, f"{where}.to")
        zone = self.zone(spec.zone, side, f"{where}.zone")
        return Promotion(zone or frozenset(range(self.geo.size)), tuple(spec.to), spec.optional, spec.ends_turn)

    def _symbols(self, kinds: List[str]) -> Dict[Tuple[int, str], str]:
        out: Dict[Tuple[int, str], str] = {}
        for side, spec in enumerate(self.specs):
            for kind in kinds:
                piece = self.c.pieces[kind]
                if isinstance(piece.symbol, dict):
                    unknown = set(piece.symbol) - set(self.sides)
                    if unknown:
                        raise MechanismError(f"symbol for unknown side(s) {sorted(unknown)}", f"sides: {', '.join(self.sides)}",
                                             f"pieces.{kind}.symbol")
                    symbol = piece.symbol.get(spec.id)
                elif len(kinds) == 1 and piece.symbol is None:
                    symbol = spec.mark or spec.id[0].upper()
                else:
                    base = piece.symbol or kind[0]
                    symbol = base.upper() if side == 0 else base.lower()
                if symbol is None or len(symbol) != 1 or symbol in ".0123456789/ ":
                    raise MechanismError(f"'{kind}' of {spec.id} needs a one-character symbol (not a digit, '.', '/')",
                                         "set the piece's symbol, e.g. {\"symbol\": {\"white\": \"o\", \"black\": \"x\"}}",
                                         f"pieces.{kind}.symbol")
                out[(side, kind)] = symbol
        seen: Dict[str, Tuple[int, str]] = {}
        for key, symbol in out.items():
            if symbol in seen:
                a, b = seen[symbol], key
                raise MechanismError(
                    f"{self.sides[a[0]]} {a[1]} and {self.sides[b[0]]} {b[1]} both print as '{symbol}'",
                    "give the pieces distinct symbols (or the sides distinct marks)", "pieces")
            seen[symbol] = key
        return out

    def _place(self, rules: Rules, spec: PlaceSpec) -> None:
        kinds = spec.kinds or list(self.c.pieces)
        for kind in kinds:
            self._kind(kind, "place.kinds")
        rules.place_kinds = tuple(kinds)
        rules.place_from = spec.from_
        rules.place_zone = [self.zone(spec.where, side, "place.where") for side in range(len(self.sides))]
        if spec.gravity is not None:
            rules.gravity = self.geo.step[self.geo.expand(spec.gravity, "place.gravity")[0]]

    def _capture(self, spec: CaptureSpec, where: str) -> CaptureRule:
        geo = self.geo
        default = {"custodial": "orthogonal", "flip": "all", "enclose": "all"}[spec.rule]
        dirs = geo.expand(spec.dirs or default, f"{where}.dirs") if geo.groups or spec.dirs else ()
        for kind in spec.immune:
            self._kind(kind, f"{where}.immune")
        hostile = self.zone(spec.hostile, 0, f"{where}.hostile") or frozenset()
        return CaptureRule(spec.rule, tuple(geo.step[d] for d in dirs), hostile, frozenset(spec.immune),
                           spec.required, spec.suicide, spec.ko)

    def _castle(self, spec: CastleSpec, where: str) -> Castle:
        geo = self.geo
        side = self._side(spec.side, f"{where}.side")
        king, rook = geo.cell(spec.king, f"{where}.king"), geo.cell(spec.rook, f"{where}.rook")
        king_to, rook_to = geo.cell(spec.king_to, f"{where}.king_to"), geo.cell(spec.rook_to, f"{where}.rook_to")
        path = _between(geo, king, rook)
        if path is None:
            raise MechanismError("the king and rook must stand on one straight line", "check the castling cells", where)
        crossing = _between(geo, king, king_to)
        if crossing is None and king != king_to:
            raise MechanismError("the king must castle along a straight line", "check king_to", f"{where}.king_to")
        empty = (set(path) | {king_to, rook_to}) - {king, rook}
        safe = (king, *(crossing or []), king_to) if king != king_to else (king,)
        text = spec.name or f"{geo.names[king]}-{geo.names[king_to]}"
        return Castle(side, king, rook, king_to, rook_to, text, frozenset(empty), tuple(dict.fromkeys(safe)))

    # -- references ------------------------------------------------------------------

    def _kind(self, kind: str, where: str) -> str:
        if kind not in self.c.pieces:
            raise MechanismError(f"'{kind}' is not a piece kind", f"kinds: {', '.join(self.c.pieces)}", where)
        return kind

    def _side(self, side: str, where: str) -> int:
        if side not in self.sides:
            raise MechanismError(f"'{side}' is not a side", f"sides: {', '.join(self.sides)}", where)
        return self.sides.index(side)

    def zone(self, spec: Optional[Zone], side: int, where: str, depth: int = 0) -> Optional[FrozenSet[int]]:
        """The cells of a zone for one side, or None for "anywhere"."""
        geo = self.geo
        if spec is None:
            return None
        if depth > 16:
            raise MechanismError("zones refer to each other in a loop", "make each zone a list of cells", where)
        if isinstance(spec, list):
            return frozenset(geo.cell(cell, where) for cell in spec)
        if isinstance(spec, dict):
            unknown = set(spec) - set(self.sides)
            if unknown:
                raise MechanismError(f"zone for unknown side(s) {sorted(unknown)}", f"sides: {', '.join(self.sides)}", where)
            part = spec.get(self.sides[side])
            return frozenset() if part is None else self.zone(part, side, where, depth + 1)
        text = spec.strip()
        if text == "all":
            return frozenset(range(geo.size))
        if text in self.c.zones:
            return self.zone(self.c.zones[text], side, f"zones.{text}", depth + 1)
        if text in ("far", "near"):
            forward = self._forward(side)
            if forward is None:
                raise MechanismError(f"'{text}' needs sides with a forward direction", "list the cells instead", where)
            way = forward if text == "far" else geo.turn(forward, len(geo.dirs) // 2)
            return frozenset(i for i in range(geo.size) if geo.step[way][i] < 0)
        match = re.match(r"^(rank|row|file|col)\s+(\S+)$", text)
        if match and geo.shape in ("grid", "hex"):
            return self._line_zone(match.group(1), match.group(2), where)
        if "=" in text:
            key, _, value = text.partition("=")
            return frozenset(geo.cell(cell, f"cells.{cell}") for cell, props in self.c.cells.items()
                             if str(props.get(key.strip())) == value.strip())
        raise MechanismError(f"'{spec}' is not a zone", "use a list of cells, all, far, near, \"rank 8\", \"file a\", "
                             f"\"prop=value\" or a declared zone ({', '.join(self.c.zones) or 'none declared'})", where)

    def _line_zone(self, axis: str, value: str, where: str) -> FrozenSet[int]:
        geo = self.geo
        if axis in ("file", "col"):
            column = ord(value.lower()) - ord("a") if value.isalpha() and len(value) == 1 else _int(value, where) - 1
            index = 1 if geo.shape == "grid" else 0
            return frozenset(i for i, xy in enumerate(geo.coords) if xy[index] == column)
        number = _int(value, where)
        if geo.shape == "grid":
            row = geo.rows - number if self.c.coords == "algebraic" else number - 1
            return frozenset(i for i, (r, _) in enumerate(geo.coords) if r == row)
        return frozenset(i for i, (_, r) in enumerate(geo.coords) if r == number - 1)


def parse_setup(rules: Rules, spec: Union[str, Mapping[str, Mapping[str, List[str]]]],
                where: str = "setup") -> List[Tuple[int, str, int]]:
    """``(side, kind, cell)`` for every piece of a position: board-symbol rows (grid) or {side: {kind: [cells]}}."""
    geo = rules.geo
    if not spec:
        return []
    out: List[Tuple[int, str, int]] = []
    if isinstance(spec, Mapping):
        for side_id, kinds in spec.items():
            if side_id not in rules.sides:
                raise MechanismError(f"'{side_id}' is not a side", f"sides: {', '.join(rules.sides)}", f"{where}.{side_id}")
            if not isinstance(kinds, Mapping):
                raise MechanismError("give each side's pieces as {kind: [cells]}", "e.g. {\"white\": {\"K\": [\"e1\"]}}",
                                     f"{where}.{side_id}")
            for kind, cells in kinds.items():
                if kind not in rules.kinds:
                    raise MechanismError(f"'{kind}' is not a piece kind", f"kinds: {', '.join(rules.kinds)}",
                                         f"{where}.{side_id}.{kind}")
                for cell in cells if isinstance(cells, list) else [cells]:
                    out.append((rules.sides.index(side_id), kind, geo.cell(cell, f"{where}.{side_id}.{kind}")))
    elif isinstance(spec, str):
        if geo.shape != "grid":
            raise MechanismError("a position written as rows needs a grid board", "use {side: {kind: [cells]}}", where)
        rows = spec.strip().split("/")
        if len(rows) != geo.rows:
            raise MechanismError(f"the position has {len(rows)} rows separated by '/'; the board has {geo.rows}",
                                 "write one row per board row, top row first", where)
        for r, row in enumerate(rows):
            c, number = 0, ""
            for ch in row + " ":
                if ch.isdigit():
                    number += ch
                    continue
                if number:
                    c, number = c + int(number), ""
                if ch == " ":
                    break
                if ch != ".":
                    piece = rules.by_symbol.get(ch)
                    if piece is None:
                        raise MechanismError(f"row {r + 1}: '{ch}' is not a board symbol",
                                             f"symbols: {', '.join(sorted(rules.by_symbol))}; digits and '.' are empty cells", where)
                    if c < geo.cols:
                        out.append((piece[0], piece[1], r * geo.cols + c))
                c += 1
            if c != geo.cols:
                raise MechanismError(f"row {r + 1} ('{row}') covers {c} cells; the board has {geo.cols} columns",
                                     "count digits as empty cells", where)
    else:
        raise MechanismError("a position is a text of rows or {side: {kind: [cells]}}", None, where)
    taken: set = set()
    for _, _, index in out:
        if index in taken:
            raise MechanismError(f"two pieces are placed on {geo.names[index]}", "place at most one piece per cell", where)
        taken.add(index)
    return out


def _right(forward: str) -> Tuple[int, int]:
    """Row and column change of one step to the right of ``forward`` (orthogonal forwards)."""
    return {"n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0)}[forward]


def _hex_symmetries(q: int, r: int) -> List[Tuple[int, int]]:
    out = []
    for a, b in ((q, r), (r, q)):
        for _ in range(6):
            out.append((a, b))
            a, b = -b, a + b
    return sorted(set(out))


def _between(geo: Geometry, a: int, b: int) -> Optional[List[int]]:
    """Cells strictly between two cells on one straight line, or None when they are not aligned."""
    for direction in geo.dirs:
        ray = geo.ray(a, direction)
        if b in ray:
            return ray[: ray.index(b)]
    return None


def _pair(value: List[int], where: str) -> Tuple[int, int]:
    if len(value) != 2 or not all(isinstance(v, int) for v in value):
        raise MechanismError("give the size as [rows, cols]", "e.g. \"size\": [6, 7]", where)
    return value[0], value[1]


def _int(value: str, where: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise MechanismError(f"'{value}' is not a number", "e.g. \"rank 8\"", where) from None
