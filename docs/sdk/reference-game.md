# game

## Mechanism family `game`

Game equipment: boards with enforced rules, cards, betting pots, and statuses on pieces (timed conditions that tick, modify properties and block actions).

Named the same in every mode:
- `who`: agent type that plays (for statuses: the types that carry them)
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `views`: generate the mechanism's views
- `qty`: a number of units (items, shares, cards, batches); money is `amount`
- `phase`: start | end: when in the round the mechanism's own step runs
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "game", "mode": ...`; read one with `guide('game.<mode>')`):
- `board`: Abstract board games as data: a board (grid, hex, ring, graph), seated players, piece kinds with movement
rules, and native legal-move generation, capture rules and game-end detection.
- `cards`: A deck of cards as world state: card entities with a zone (deck, hand, discard, burn or declared zones), owner and order; who sees a card is enforced by the engine (views, inspect, tools).
- `pot`: Poker-style betting: every round is one hand.
- `status`: Named statuses on entities: timed or permanent, stacking, ticking effects each round, property modifiers ($effective), blocked actions, immunity, expiry news and cleansing.

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
- `$effective(entity, prop)` — The property with every active modifier applied: (base + adds) × multipliers from statuses, kept within the property's min/max, e.g. $effective($actor, 'armor').
- `$follow_suit(hand, lead_suit)` — The cards of `hand` that follow the suit led, or the whole hand when it has none of that suit (or nothing was led).
- `$hand(player, deck?)` — The cards in a player's hand, oldest first.
- `$has_status(entity, status)` — True when the entity has the status, e.g. $has_status($actor, 'stun').
- `$poker_hand(hole, board)` — What a player's hole cards make with the board, in words: the hand, and whether it uses the hole cards ("two pair, kings and sevens, using both hole cards") or is on the board, shared by everyone.
- `$poker_rank(cards)` — The best 5-card poker hand among the cards (e.g. 2 hole cards + 5 on the board): {score, category, level, name, ranks, best}. A higher score is a better hand; equal scores split.
- `$pot_live(pot)` — Players still in the hand (not folded), in seat order.
- `$pot_options(pot, player)` — What a player may do in a betting round: {your_turn, to_call, call_amount, can_check, can_call, can_bet, min_bet, can_raise, min_raise_to, max_to, can_all_in, current_bet, pot}.
- `$pot_table(pot, viewer)` — Lines describing the table (pot, bets, stacks, who is to act) for the table view.
- `$pot_total(pot)` — Chips in the pot this hand (every player's committed chips).
- `$runs(cards, size?)` — Runs (lists) of at least `size` (default 3) consecutive ranks in one suit, longest first; an ace is high or low.
- `$sets(cards, size?)` — Groups (lists) of at least `size` (default 3) cards of one rank, for rummy-like games.
- `$status_rounds(entity, status)` — Rounds the status lasts after this one (0 = it ends this round); null when permanent or absent.
- `$status_stacks(entity, status)` — Stacks of the status on the entity (0 when it has none).
- `$status_text(entity, mechanism)` — The entity's statuses as text: 'poison ×2 (1 more round), stun (ends this round)'.
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

**GameSpec** — Who the players are and what each one scores, for `RunResult.returns`, `fg_env.rl.game` and `fg_env.rl.gym`.
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
