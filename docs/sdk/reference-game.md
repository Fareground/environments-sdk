# game

## Mechanism family `game`

Game equipment: boards with enforced rules, cards, betting pots and worker-placement slots.

Named the same in every mode:
- `who`: agent type that plays
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `views`: generate the mechanism's views
- `qty`: a number of units (items, shares, cards, batches); money is `amount`
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "game", "mode": ...`; read one with `guide('game.<mode>')`):
- `board`: Abstract board games as data: a board (grid, hex, ring, graph), seated players, piece kinds with movement
rules, and native legal-move generation, capture rules and game-end detection.
- `cards`: A deck of cards as world state: card entities with a zone (deck, hand, discard, burn or declared zones), owner and order; who sees a card is enforced by the engine (views, inspect, tools).
- `pot`: Poker-style betting: every round is one hand.
- `slots`: Worker placement: capacity-limited action spaces.

Functions:
- `$blackjack_soft(cards)` — True when the blackjack total counts an ace as 11 (a soft total).
- `$blackjack_value(cards)` — Blackjack total of the cards: aces 11 unless that busts, faces 10.
- `$board_at(board, cell)` — The piece entity on a cell, or null.
- `$board_cell(board, cell)` — A cell's declared properties (color, region, terrain …) as a map.
- `$board_in_check(board, player)` — True when the player's royal piece is attacked.
- `$board_line(board, player, length)` — True when the player has `length` pieces in a row.
- `$board_moves(board, player?)` — Legal moves of a player (default: the player to move) on a declared board, as move texts: e2-e4, b1xc3, e7-e8=Q, O-O, d3 (placement). Empty when the game is over or it is not their turn.
- `$board_render(board, viewer?)` — The board as compact text with coordinates, whose turn it is, the last move, check and the result.
- `$board_score(board)` — Each side's score as {side: points}: pieces on the board (plus surrounded area when the board scores area, plus komi).
- `$card_names(cards, ids?)` — Card names as text ('A♠, K♥'); with ids true each name is followed by its [id].
- `$card_visible(card, viewer)` — True when `viewer` may see the card's face (public zone, face up, own hand, or peeked).
- `$cards_table(deck, viewer)` — Lines describing every zone of a deck as `viewer` may see it: visible cards by name, hidden ones only counted (the viewer's own hand is left out). Used by the table view.
- `$claims(slots, space)` — The players with a worker on `space` this round.
- `$follow_suit(hand, lead_suit)` — The cards of `hand` that follow the suit led, or the whole hand when it has none of that suit (or nothing was led).
- `$hand(player, deck?)` — The cards in a player's hand, oldest first.
- `$open_spaces(slots, player)` — Names of the action spaces `player` may claim now.
- `$poker_hand(hole, board)` — What a player's hole cards make with the board, in words: the hand, and whether it uses the hole cards ("two pair, kings and sevens, using both hole cards") or is on the board, shared by everyone.
- `$poker_rank(cards)` — The best 5-card poker hand among the cards (e.g. 2 hole cards + 5 on the board): {score, category, level, name, ranks, best}. A higher score is a better hand; equal scores split.
- `$pot_live(pot)` — Players still in the hand (not folded), in seat order.
- `$pot_options(pot, player)` — What a player may do in a betting round: {your_turn, to_call, call_amount, can_check, can_call, can_bet, min_bet, can_raise, min_raise_to, max_to, can_all_in, current_bet, pot}.
- `$pot_table(pot, viewer)` — Lines describing the table (pot, bets, stacks, who is to act) for the table view.
- `$pot_total(pot)` — Chips in the pot this hand (every player's committed chips).
- `$puzzle(kind, options?)` — A new puzzle with exactly one solution, drawn from the run's seed. sudoku: options {box: 2 or 3 (default), clues: the fewest clues to keep (default 0: remove all it can)} → {puzzle, solution, clues, box}; blanks are 0.
- `$runs(cards, size?)` — Runs (lists) of at least `size` (default 3) consecutive ranks in one suit, longest first; an ace is high or low.
- `$sets(cards, size?)` — Groups (lists) of at least `size` (default 3) cards of one rank, for rummy-like games.
- `$slot_board(slots)` — Lines describing every action space: capacity, who claimed it, what it does.
- `$solve(kind, problem, mode?)` — Solve or check a puzzle. mode count (default): {solutions: 0, 1 or 2 (two or more), unique, solution}. mode check (an attempt): sudoku → {valid, complete, solved, conflicts: [[row, col]]}; exact_cover (problem.chosen: set names) → {valid, complete, solved, overlaps, missing}. sudoku problem: rows with 0 or null for blanks; exact_cover problem: {sets: {name: [elements]}, universe?, chosen?}.
- `$top_card(zone, owner?, deck?)` — The top card of a zone (the last card placed), or null.
- `$top_cards(zone, n, owner?, deck?)` — The top n cards of a zone, top first.
- `$trick_winner(cards, lead_suit?, trump?)` — The card winning a trick (cards in play order): highest trump, else highest of the suit led (default: the first card's suit). Its `played_by` is the player who played it.
- `$zone(name, owner?, deck?)` — The cards in a zone (one owner's, or everyone's), bottom first.

## `game`: GameSpec

Seats and what each scores, for tournaments, game search and gyms.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| seat | $it $i |
| returns/rewards | $actor $result (winner, ended_by) |

**GameSpec** — Who the players are and what each one scores, for `RunResult.returns`, `fg_env.game` and `fg_env.gym`.
- `players`: text | [text] — Agent type(s) whose entities are the seats (default: every agent).
- `seat`: text — Seat order: an expression over $it, lowest first (default: the order entities are created).
- `returns`: text — A seat's total return so far: an expression over $actor, read after every decision and at the end.
- `rewards`: text — A seat's reward for its latest step: an expression over $actor (default: the change in `returns` since that seat's previous step).
- `utility`: text = "general_sum" — One of: zero_sum, constant_sum, general_sum, identical — checked on every finished run.
- `total`: number — constant_sum: what every finished run's returns add up to.
- `min_return`: number — The lowest return any seat can finish with — checked on every finished run.
- `max_return`: number — The highest return any seat can finish with — checked on every finished run.
- `dynamics`: text — Claim, verified by check: sequential | simultaneous | scheduled | mixed.
- `chance_mode`: text — Claim, verified by check: deterministic | explicit (only `chance` effects, whose outcomes are listed) | sampled.
- `information`: text — Claim, verified by check: perfect | imperfect.
- `num_players`: int — Claim, verified by check: agents at the start.
- `min_players`: int — Claim, verified by check.
- `max_players`: int — Claim, verified by check.
- `max_rounds`: int — Claim, verified by check: the longest a run lasts, in rounds.
- `action_space`: text — Claim, verified by check: finite | parametric.
- `num_distinct_actions`: int — Claim, verified by check.
