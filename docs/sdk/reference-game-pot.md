# game / pot

### `game.pot`
Poker-style betting: every round is one hand. Generates player chips (stack, bet, committed, folded, in_hand), `<name>_fold` / `_check` / `_call` / `_bet` / `_raise` / `_all_in` tools with legal amounts in their schemas, one sequential stage per street that wakes exactly the player to act, blinds and antes, side pots and a showdown paying each pot to its best `score`. State: $world.<name>_to_act, _current_bet, _min_raise, _button, _result. Functions: $pot_options, $pot_live, $pot_total. An `end` condition about stacks must also require $pot_total(<name>) == 0, or it fires while the chips of an all-in hand are still in the pot.

Config:
- `who` (required): Agent type that bets (subtypes included).
- `stack` (default 1000): Starting chips (number or expression).
- `seat` (default null): Seat order: an expression over $it, lowest first (default: declaration order).
- `blinds` (default null): [small, big] blinds posted each hand (numbers or expressions).
- `ante` (default 0): Chips every player puts in before the deal.
- `min_bet` (default null): Smallest bet or raise size (default: the big blind, else 1).
- `streets` (default {"betting": []}): Betting rounds in order: {stage name: effects run before that round's betting (deal the flop …)}.
- `setup` (default []): Effects at the start of each hand, before blinds (collect and deal cards).
- `before_showdown` (default []): Effects before a contested showdown (reveal hands).
- `score` (required): A player's showdown score ($it), higher wins, e.g. "$poker_rank($hand($it) + $zone(board)).score".
- `label` (default null): Text naming a player's holding at showdown ($it), e.g. "$poker_rank(...).name".
- `max_raises` (default null): Bets and raises allowed per betting round (default unlimited).
- `max_calls` (default 6): Tool calls per betting turn.
- `conserve` (default true): Add the invariant that chips are never created or destroyed.
- `views` (default true): Generate the table view.

Actions of the `game` op:
- `fold` — takes `who`: {"game": "table", "action": "fold"}  (give up the hand; for $actor or `who`; an illegal move fails the action)
- `check` — takes `who`: {"game": "table", "action": "check"}  (stay in without adding chips; for $actor or `who`; an illegal move fails the action)
- `call` — takes `who`: {"game": "table", "action": "call"}  (match the highest bet (all-in when short); for $actor or `who`; an illegal move fails the action)
- `bet` — takes `who`, `amount` (needs `amount`): {"game": "table", "action": "bet", "amount": "$params.amount"}  (open the betting with `amount` chips; for $actor or `who`; an illegal move fails the action)
- `raise` — takes `who`, `to` (needs `to`): {"game": "table", "action": "raise", "to": "$params.to"}  (raise to a total bet of `to` chips this betting round; for $actor or `who`; an illegal move fails the action)
- `all_in` — takes `who`: {"game": "table", "action": "all_in"}  (put every chip in; for $actor or `who`; an illegal move fails the action)

```json
{"mechanisms": {"my_pot": {"kind": "game", "mode": "pot", "who": "player", "stack": 500, "blinds": [5, 10], "seat": "$it.seat", "streets": {"preflop": [], "flop": [{"game": "cards", "action": "deal", "qty": 3, "zone": "board"}]}, "setup": [{"game": "cards", "action": "collect"}, {"game": "cards", "action": "deal", "qty": 2, "to": "$filter(player, $it.in_hand)"}], "score": "$poker_rank($hand($it) + $zone(board)).score"}}}
```
