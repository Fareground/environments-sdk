# game / cards

### `game.cards`
A deck of cards as world state: card entities with a zone (deck, hand, discard, burn or declared zones), owner and order; who sees a card is enforced by the engine (views, inspect, tools). Generates the card type, the cards, round-1 setup (shuffle and deal), optional `<name>_play` / `<name>_discard` / `<name>_draw` / `<name>_give` tools listing only legal cards, and views of your hand and the table. Functions: $hand, $zone, $top_card, $top_cards, $card_names, $poker_rank, $blackjack_value, $sets, $runs, $trick_winner, $follow_suit.

Config:
- `who` (required): Agent type holding hands (subtypes included).
- `type` (default "card"): Entity type of the cards.
- `deck` (default "standard"): 'standard' (52 cards, ids like AS 10H, ranks 2–14, suits spades hearts diamonds clubs) or a list of card entries.
- `jokers` (default 0): Jokers added to a standard deck.
- `props` (default {}): Extra card property specs.
- `zones` (default {}): Extra zones (or changes to deck, hand, discard, burn): a visibility or {visible, owned, show, title}.
- `personal` (default false): Every player has their own draw pile and discard pile (deck-builders).
- `hand_size` (default 0): Cards dealt to each player (number or expression).
- `deal` (default "start"): start: shuffle and deal once in round 1 | round: collect, shuffle and deal every round | never.
- `deal_to` (default null): Which players are dealt in ($it), e.g. "$it.chips > 0".
- `after_deal` (default []): Effects right after each deal (flip a starting card …).
- `reshuffle` (default true): An empty draw pile is refilled by shuffling the discard pile.
- `keep_top` (default false): The discard pile's top card stays when it is reshuffled.
- `play` (default null): Generate `<name>_play` (a card from your hand to a zone).
- `discard` (default null): Generate `<name>_discard`.
- `draw` (default null): Generate `<name>_draw`.
- `give` (default null): Generate `<name>_give` (give a card to another player, privately).
- `views` (default true): Generate the hand and table views.

Nested config:
**CardEntry** — One card, or a family of cards (every suit × every rank), with copies.
- `name`: text — Card name; may use {suit} and {rank}. Default '<suit> <rank>'.
- `suit`: text
- `rank`: any
- `suits`: [text] — Make one card per suit (combined with ranks).
- `ranks`: [any] — Make one card per rank (combined with suits).
- `copies`: int = 1
- `per_player`: bool = false — Every player gets their own copies (starting decks); created in round 1.
- `zone`: text = "deck" — Where the card starts.
- `props`: object — Extra card properties: cost, text, effect …
**ZoneConfig** — A place cards can be.
- `visible`: any = "public" — public (everyone sees the cards) | owner (only the zone's owner) | hidden (nobody).
- `owned`: bool — Every player has their own copy of this zone (like a hand).
- `show`: any = "all" — What the table view shows: every card, the top card, or the count.
- `title`: text — Name shown in views.
**CardActionConfig** — A generated card tool. ``true`` takes every default.
- `description`: text
- `to`: text — Zone the card goes to (play: default discard).
- `where`: expression — Which cards of your hand qualify ($it the card, $actor): only these are offered.
- `when`: [any] — Extra requirements for the tool, as in actions.
- `params`: object — Extra tool arguments.
- `do`: effects — Effects after the card moves ($params.card is the card).
- `qty`: int | text = 1 — Cards drawn (draw).
- `terminal`: bool | text = true
- `announce`: text
- `outcome`: text

Actions of the `game` op:
- `shuffle` — takes `zone`, `owner`: {"game": "cards", "action": "shuffle", "zone": "deck"}  (random order, face down; an owned zone shuffles each pile, or only `owner`'s)
- `collect` — takes `zones`: {"game": "cards", "action": "collect"}  (every card back into the draw pile, face down, shuffled; `zones` limits which)
- `deal` — takes `qty`, `to`, `zone`, `face_up`, `from`: {"game": "cards", "action": "deal", "qty": 2, "to": "$filter(player, $it.chips > 0)"}  (round-robin from the top; no `to`: every player, or the zone itself when it is shared: {"game": "cards", "action": "deal", "qty": 3, "zone": "board"})
- `draw` — takes `qty`, `who`, `zone`, `face_up`: {"game": "cards", "action": "draw", "qty": 1}  (for $actor, or `who`; an empty draw pile is refilled from the discard pile; the cards are in $world.<deck>_drawn)
- `burn` — takes `qty`: {"game": "cards", "action": "burn", "qty": 1}  (top cards of the draw pile to the hidden burn zone)
- `move` — takes `cards`, `to`, `owner`, `face_up`, `bottom` (needs `cards`, `to`): {"game": "cards", "action": "move", "cards": "$params.card", "to": "tableau", "owner": "$actor", "face_up": true}  (onto the top, or the bottom)
- `play` — takes `cards`, `to` (needs `cards`): {"game": "cards", "action": "play", "cards": "$params.card", "to": "trick"}  (face up onto a zone; default the discard pile)
- `discard` — takes `cards` (needs `cards`): {"game": "cards", "action": "discard", "cards": "$params.card"}  (onto the discard pile; its owner's pile with personal decks)
- `give` — takes `cards`, `to`, `zone` (needs `cards`, `to`): {"game": "cards", "action": "give", "cards": "$params.card", "to": "$params.to"}  (into another player's hand; only the two of them see which)
- `reveal` — takes `cards`, `to`, `say` (needs `cards`): {"game": "cards", "action": "reveal", "cards": "$hand($it)"}  (face up for everyone; with `to`, shown only to those players)
- `peek` — takes `cards`, `to`, `say` (needs `cards`): {"game": "cards", "action": "peek", "cards": "$top_cards(deck, 3)", "to": "$actor"}  (only `to` (default $actor) sees the cards, from now on)

```json
{"types": {"player": {"agent": true}}, "entities": {"player": {"type": "player", "count": 2}}, "mechanisms": {"my_cards": {"kind": "game", "mode": "cards", "who": "player", "hand_size": 7, "keep_top": true, "after_deal": [{"game": "my_cards", "action": "deal", "qty": 1, "zone": "discard"}], "play": {"where": "$it.suit == $top_card(discard).suit or $it.rank == $top_card(discard).rank"}, "draw": true}}}
```
