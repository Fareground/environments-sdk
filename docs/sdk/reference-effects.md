# effects

## Effects (actions.do, events.do)

Assignment text:
* `"$actor.cash -= $params.qty * $params.offer.price"` — also `=`, `+=`, `*=`, `/=`; targets are
  entity props (`$actor.x`, `$params.offer.x`, `$it.x`), `$world.x`, `$physics.x`.
* `"$total = $params.qty * 2"` — a local (`$total`) usable by later effects and the outcome.
* `+=`/`-=` on a list prop append/remove an item; `-=` removes one copy per item (`[1, 2, 2] -= 2` leaves
  `[1, 2]`), comparing like `==`.
* Element assignment: `"$world.board[$i] = $actor.mark"`, `"$actor.scores[round_2] += 1"` (lists and maps).
* Links: `"$link($actor, $params.who, trusts).value += 0.1"`, `"$link($actor, $params.who, trusts).since = $round"`
  (the link must exist; its value keeps to the relation's min/max and fields are typed, like props).
* A write past a numeric prop's, link value's or layer cell's min/max is refused, like a transfer that does not
  fit: an action is rolled back and its actor told why; world logic (an event) that does it fails
  the run at its path. To saturate, say so: `$clamp(x, low, high)`.
  Types are enforced: null too, which only a prop declared with `"default": null` (or no default) may hold.

Operation objects (exactly one operation key each):
- `if`: {"if": "$cost > $actor.cash", "then": [...], "else": [...]}
- `each`: {"each": "offer", "where": "$it.stock == 0", "do": ["$it.listed = false"]}  (with "as": "o", write $o instead of $it; "sync": true — every item reads the world as it was before the loop and all their writes land together, for cellular automata and simultaneous updates: only property and layer-cell assignments, and two items writing different values to one property is an error)
- `create`: {"create": "review", "count": 1, "name": "Review {$i}", "props": {"stars": "$params.stars"}, "at": null, "as": "made"}  (in `props`, `$it` is the new entity, so a prop can read an earlier one: "double": "$it.base * 2"; inside a loop, name the loop's item with `as` to read it there)
- `remove`: {"remove": "$params.target"}
- `transfer`: {"transfer": "cash", "from": "$actor", "to": "$params.seller", "amount": 10}  (fails the action if short; in world logic, the run)
- `link`: {"link": "trusts", "from": "$actor", "to": "$params.who", "value": 0.8, "props": {"since": "$round"}}  (creates or updates: without `value` an existing link keeps its value and a new one gets the relation's `default`; `props` sets link fields, a new link starting from their defaults)
- `unlink`: {"unlink": "follows", "from": "$actor", "to": "$params.who"}
- `move`: {"move": "$actor", "to": "$params.place"}
- `post`: {"post": "chat", "text": "$params.text", "to": "$params.who", "delay": 2, "drop": 0.1}  (record fields as keys; to = private recipients; optional `delay` in rounds and `drop` chance)
- `emit`: {"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)", "data": {}, "delay": 1}  (optional `delay` and `drop`, as for post)
- `fail`: {"fail": "You cannot afford that."}  (roll back the action; text goes to the actor; in world logic it fails the run)
- `end`: {"end": "bankrupt", "winner": "$top(player, $it.score, 1)[0]", "say": "..."}
- `after`: {"after": 3, "do": [...]}  (runs 3 rounds later with the same locals)
- `wake`: {"wake": "$params.who", "why": "{$actor.name} asked you a question."}  (a turn later; "now": true — they react as soon as this action has taken effect, before this turn continues, offered the actions named in "actions": ["accept", "reject"] (without it, every action of the current stage) (a reaction cannot stop or change the action that woke them: to let others answer first, use a procedure stack; reactions set off more than 4 deep wait for a normal turn); a wake on a later round goes inside `after`)
- `repeat`: {"repeat": "$count(order)", "while": "$count(order) > 1", "do": [...]}  (limit may be an expression; derive it from the data, not an arbitrary constant; 0 runs nothing; error if still true at the limit)
- `call`: {"call": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}  (runs a def's `do` effects with those arguments)
- `chance`: {"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails", "do": [...]}], "as": "coin"} or {"chance": "deal", "outcomes": "$world.deck", "weight": "1", "as": "card", "do": [...]}  (picks one outcome from the listed distribution, logged as a `chance` event; `fg_env.rl.game` can enumerate and choose outcomes instead of sampling them)
- `layer`: {"layer": "sugar", "set": "$min($value + 1, 4)"}  (every cell: `$cell` is its position, `$value` its value, all reading the old values; `"at": "$it.at"` sets one cell, `"where"` limits which) · {"layer": "scent", "diffuse": 0.1} (each cell hands that share out to its neighbours; with `"where"` only the cells it holds for take part, the others are walls that keep what would cross them) · {"layer": "scent", "decay": 0.05}
- `market`: {"market": "<market mechanism>", "action": ...} — actions: order_book buy sell cancel cancel_all algo open close; prediction buy sell resolve; auction bid ask; posted buy offer accept rate set_price promote sponsor (guide("market"))
- `economy`: {"economy": "<economy mechanism>", "action": ...} — actions: inventory give make use drop pickup; ledger pay mint burn lend repay; production start; supply_chain order; demand receive remove; replenishment order; queue — (guide("economy"))
- `agreements`: {"agreements": "<agreements mechanism>", "action": ...} — actions: bookings book cancel; negotiation propose counter accept reject withdraw fulfill; subscriptions subscribe set_price (guide("agreements"))
- `decision`: {"decision": "<decision mechanism>", "action": ...} — actions: ballot tally; deliberation speak ready raise_hand recognize yield propose second amend withdraw call_question vote; procedure push pass counter (guide("decision"))
- `game`: {"game": "<game mechanism>", "action": ...} — actions: board move pass setup; cards shuffle collect deal draw burn move play discard give reveal peek; pot fold check call bet raise all_in; status apply cleanse (guide("game"))
- `groups`: {"groups": "<groups mechanism>", "action": ...} — actions: roles eliminate reveal; matching — (guide("groups"))
- `social`: {"social": "<social mechanism>", "action": ...} — actions: diffusion step seed adopt reject expose; feed post reply repost react follow unfollow befriend unfriend block unblock mute unmute label (guide("social"))
- `host`: {"host": "<host mechanism>", "action": ...} — actions: judge judge; game_master resolve; personas write; tool call; memory note recall; recap write; feed — (guide("host"))

### Ordered processing

`each` has no `order` field. Pass a sorted collection to `each` instead:

```json
{"each": "$sort(item, [$it.due, $it.sequence])", "do": "$world.seen += $it.id"}
```

Keys are compared in order: earliest `due`, then lowest `sequence`. Use list keys
for priority and tie-breaking; multiplying a key by a large constant can change
priority when the second key grows. Negate a numeric key for descending order.
The list is selected once before the loop; effects still see current entity
properties. Filter with `$filter` when needed. For one highest-ranked item use
`$best(items, [key1, key2])`: it always gives one item, breaking an exact tie at
random (seeded); add a unique last key when the rule needs a fixed order, or use
`$best(items, key, 'all')` for the list of every item tied for best.

