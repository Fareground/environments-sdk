# game / board

### `game.board`
Abstract board games as data: a board (grid, hex, ring, graph), seated players, piece kinds with movement
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
piece per cell (no stacks), dice are not built in.

Config:
- `shape` (default "grid"): grid | hex | ring | graph.
- `size` (default 8): grid: n or [rows, cols]; hex: radius or [rows, cols]; ring: cells.
- `coords` (default "algebraic"): grid cell names: algebraic (a1 bottom-left) or rc (row,col from the top-left).
- `wrap` (default true): ring: the last cell joins the first (false = a track).
- `nodes` (default []): graph: place names.
- `edges` (default []): graph: [a, b] or [a, b, dir, back_dir].
- `cells` (default {}): Cell properties: {cell: {color: dark, terrain: …}}; zones can select them ("color=dark").
- `zones` (default {}): Named cell sets: a list, "rank 8", "file a", "far", "near", "prop=value", another zone, or {side: zone}.
- `sides` (required): Player entity ids in seat order; the first moves first.
- `who` (default "player"): Agent type of the players (declared for you when missing).
- `piece_type` (default "piece"): Entity type of the pieces (props owner, kind, cell, moved).
- `pieces` (required): Piece kinds: {kind: {name, symbol, moves, royal, promote, irreversible}}.
- `setup` (default ""): Start position: grid rows top to bottom as board symbols ("rnbqkbnr/pppppppp/8/…"), or {side: {kind: [cells]}}.
- `hand` (default {}): Pieces in reserve at the start: {side: {kind: count}} (with place.from hand).
- `place` (default null): Players may place pieces on empty cells: {kinds, from, where, gravity}.
- `captures` (default []): Capture rules after each move: custodial | flip | enclose.
- `castling` (default []): Castling moves: [{side, king, rook, king_to, rook_to, name}].
- `mandatory_capture` (default false): When a capture is available, only captures are legal.
- `self_check` (default false): A move may not leave your royal piece attacked (check, checkmate, stalemate).
- `allow_pass` (default false): Players may pass (a `<name>_pass` tool).
- `no_moves` (default "lose"): When the player to move has no legal move: lose (checkmate), draw, or pass automatically.
- `stalemate` (default "draw"): No legal move while the royal piece is not attacked.
- `line` (default null): Win by n of your pieces in a row.
- `lines` (default []): Explicit winning lines (cells) instead of straight rows.
- `repetition` (default null): Draw when the same position occurs this many times.
- `move_limit` (default null): Draw after this many moves in a row without a capture or irreversible move (100 = the 50-move rule).
- `passes_end` (default null): Consecutive passes that end the game (default: one per side).
- `score` (default "none"): Winner when the game ends by passes or when nobody can move: pieces on the board, or area (pieces + surrounded empty cells).
- `komi` (default {}): Points added to a side's score: {white: 6.5}.
- `stage` (default null): Play during this declared stage instead of a generated one.

Nested config:
**SideSpec** — A seat at the board: a player entity id.
- `id`: text (required)
- `name`: text
- `forward`: text — Direction this side's pieces advance (grid default: n for the first side, s for others).
- `mark`: text — Board symbol when there is a single piece kind (default: first letter of the id).
**PieceSpec** — A piece kind.
- `name`: text — Readable name (default: the kind key).
- `symbol`: text | object — One-character board symbol, or {side: symbol}.
- `moves`: [MoveSpec]
- `royal`: bool = false — Losing it loses the game; with self_check it may not be left attacked.
- `promote`: PromoteSpec
- `irreversible`: bool = false — Its moves reset the move_limit and repetition count (pawns).
**MoveSpec** — One movement pattern of a piece kind. Exactly one of step, slide, leap, jump.
- `step`: text | [text] — Move `distance` cells in these directions through empty cells ("adjacent": to any neighbouring cell, e.g. on graphs).
- `slide`: text | [text] — Move any number of cells along these directions until blocked.
- `leap`: [any] — Jump to an offset [forward, right] (or a list of them), ignoring what is between; every mirror and rotation unless `exact`.
- `jump`: text | [text] — Hop over the adjacent piece in these directions onto the empty cell beyond.
- `distance`: int = 1 — step: exact number of cells (cells passed over must be empty).
- `max`: int — slide: at most this many cells.
- `only`: any — move: only onto empty cells; capture: only onto enemies.
- `first`: bool = false — Only for a piece that has not moved yet.
- `from`: text | [text] | object — Only from cells in this zone.
- `within`: text | [text] | object — Only onto cells in this zone.
- `over`: any = "enemy" — jump: which pieces may be jumped.
- `capture`: bool — jump: remove the jumped piece (default: when jumping enemies).
- `chain`: bool = false — After a capture with this pattern the same piece keeps capturing while it can (multi-jump).
- `passable`: bool = false — A multi-cell step that an en_passant pattern can capture on the cell passed over.
- `en_passant`: bool = false — May also capture a piece that just made a `passable` step over the target cell.
- `exact`: bool = false — leap: use the offsets as given, relative to the side's forward direction.
**PromoteSpec** — Promotion: a piece ending a move in `zone` becomes one of `to`.
- `zone`: text | [text] | object = "far" — Where promotion happens (default: the far edge for the side).
- `to`: [text] (required) — Piece kinds it may become; several = the mover chooses (e7-e8=Q).
- `optional`: bool = false — The mover may also decline promotion.
- `ends_turn`: bool = true — Promotion ends a capture chain.
**PlaceSpec** — Placing new pieces on empty cells (Go, Othello, Connect Four, drops).
- `kinds`: [text] — Kinds that can be placed (default: every kind).
- `from`: any = "supply" — supply: unlimited; hand: counts in `hand`.
- `where`: text | [text] | object — Only on cells in this zone.
- `gravity`: text — A placed piece falls this way to the last empty cell ("s" for Connect Four).
**CaptureSpec** — A capture rule applied after every move or placement.
- `rule`: any (required) — custodial: sandwich an enemy between the moved piece and a friend; flip: turn over enemy lines closed by a friend (Othello); enclose: remove groups without liberties (Go).
- `dirs`: text | [text] — custodial default orthogonal; flip default all.
- `hostile`: text | [text] | object — custodial: empty cells that also close a sandwich (throne, corners).
- `immune`: [text] — custodial: kinds that cannot be sandwiched.
- `required`: bool = false — flip: a placement must flip at least one piece.
- `suicide`: bool = false — enclose: a move may leave its own group without liberties (removing it).
- `ko`: bool = true — enclose: simple ko — a single stone cannot be retaken at once.
**CastleSpec** — A castling move: royal `king` and `rook` both unmoved, cells between empty, the king not in check nor passing attacked cells.
- `side`: text (required)
- `king`: text (required)
- `rook`: text (required)
- `king_to`: text (required)
- `rook_to`: text (required)
- `name`: text — Notation (default: the king's move, e.g. e1-g1).

Actions of the `game` op:
- `move` — takes `text` (needs `text`): {"game": "chess", "action": "move", "text": "$params.move"}  (play a legal move for the side to move: captures, promotion, capture rules, chains, turn, and game-end detection; fails if illegal)
- `pass`: {"game": "go", "action": "pass"}  (the side to move passes; enough passes in a row end the game by score)
- `setup` — takes `position`, `turn` (needs `position`): {"game": "chess", "action": "setup", "position": "$inputs.start", "turn": "black"}  (replace every piece with a position — board-symbol rows or {side: {kind: [cells]}} — and restart the game state)

```json
{"types": {"player": {"agent": true, "props": {"side": {"type": "enum", "values": ["x", "o"], "default": "x"}}}}, "entities": {"xena": {"type": "player", "props": {"side": "x"}}, "otto": {"type": "player", "props": {"side": "o"}}}, "mechanisms": {"my_board": {"kind": "game", "mode": "board", "size": [3, 3], "sides": ["x", "o"], "pieces": {"mark": {}}, "place": {}, "line": 3, "no_moves": "draw"}}}
```
