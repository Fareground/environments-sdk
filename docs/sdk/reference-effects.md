# effects

## Effects (actions.do/otherwise, events.do, stages.on_enter/on_exit)

Assignment text:
* `"$actor.cash -= $params.qty * $params.offer.price"` — also `=`, `+=`, `*=`, `/=`; targets are
  entity props (`$actor.x`, `$params.offer.x`, `$it.x`), `$world.x`, `$physics.x`.
* `"$total = $params.qty * 2"` — a local (`$total`) usable by later effects and the outcome.
* `+=`/`-=` on a list prop append/remove an item.
* Element assignment: `"$world.board[$i] = $actor.mark"`, `"$actor.scores[round_2] += 1"` (lists and maps).
* Links: `"$link($actor, $params.who, trusts).value += 0.1"`, `"$link($actor, $params.who, trusts).since = $round"`
  (the link must exist; its value is clamped to the relation's min/max, fields are typed like props).
* A write past a numeric prop's min/max is refused, like a transfer that does not fit: an action is rolled
  back and its actor told why; world logic (an event, a stage hook) that does it fails the run at its path.
  To saturate, say so: `$clamp(x, low, high)`.
  Types are enforced.

Operation objects (exactly one operation key each):
- `if`: {"if": "$cost > $actor.cash", "then": [...], "else": [...]}
- `each`: {"each": "offer", "where": "$it.stock == 0", "do": ["$it.listed = false"]}  (with "as": "o", write $o instead of $it)
- `create`: {"create": "review", "count": 1, "name": "Review {$i}", "props": {"stars": "$params.stars"}, "at": null, "as": "made"}
- `remove`: {"remove": "$params.target"}
- `transfer`: {"transfer": "cash", "from": "$actor", "to": "$params.seller", "amount": 10}  (fails the action if short)
- `link`: {"link": "trusts", "from": "$actor", "to": "$params.who", "value": 0.8, "props": {"since": "$round"}}  (creates or updates: without `value` an existing link keeps its value and a new one gets the relation's `default`; `props` sets link fields, a new link starting from their defaults)
- `unlink`: {"unlink": "follows", "from": "$actor", "to": "$params.who"}
- `move`: {"move": "$actor", "to": "$params.place"}
- `post`: {"post": "chat", "text": "$params.text", "to": "$params.who", "delay": 2, "drop": 0.1}  (record fields as keys; to = private recipients; optional `delay` — rounds, or time on a continuous clock — and `drop` chance)
- `emit`: {"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)", "data": {}, "delay": 1}  (optional `delay` and `drop`, as for post)
- `fail`: {"fail": "You cannot afford that."}  (roll back the action; text goes to the actor)
- `end`: {"end": "bankrupt", "winner": "$top(player, $it.score, 1)[0]", "say": "..."}
- `after`: {"after": 3, "do": [...]}  (runs 3 rounds later with the same locals; on a continuous clock, 3 time units later)
- `wake`: {"wake": "$params.who", "why": "{$actor.name} asked you a question."}  (a turn later; "now": true — they react as soon as this action has taken effect, before this turn continues (a reaction cannot stop or change the action that woke them: to let others answer first, use a procedure stack); "in": 5 — continuous clock, that much later; "drop": 0.2 — the wake may be lost)
- `repeat`: {"repeat": "$count(order)", "while": "$count(order) > 1", "do": [...]}  (limit may be an expression; derive it from the data, not an arbitrary constant; 0 runs nothing; error if still true at the limit)
- `block`: {"block": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}  (runs a named effect list from `blocks`)
- `chance`: {"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails", "do": [...]}], "as": "coin"} or {"chance": "deal", "outcomes": "$world.deck", "weight": "1", "as": "card", "do": [...]}  (picks one outcome from the listed distribution, logged as a `chance` event; `fg_env.game` can enumerate and choose outcomes instead of sampling them)
- `layer`: {"layer": "sugar", "set": "$min($value + 1, 4)"}  (every cell: `$cell` is its position, `$value` its value, all reading the old values; `"at": "$it.at"` sets one cell, `"where"` limits which) · {"layer": "scent", "diffuse": 0.1} (each cell hands that share out to its neighbours) · {"layer": "scent", "decay": 0.05}
- `market`: {"market": "<market mechanism>", "action": ...} — actions: order_book buy sell cancel cancel_all algo rebase open close; prediction buy sell resolve; auction bid ask rebase; posted buy offer accept rate set_price promote sponsor (guide("market"))
- `economy`: {"economy": "<economy mechanism>", "action": ...} — actions: inventory give make use drop pickup; ledger pay mint burn lend repay; production start; supply_chain order; demand receive remove; replenishment order (guide("economy"))
- `agreements`: {"agreements": "<agreements mechanism>", "action": ...} — actions: bookings book cancel; labor hire quit fire; negotiation propose counter accept reject withdraw fulfill; subscriptions subscribe set_price (guide("agreements"))
- `decision`: {"decision": "<decision mechanism>", "action": ...} — actions: ballot tally; deliberation speak ready raise_hand recognize yield propose second amend withdraw call_question vote (guide("decision"))
- `game`: {"game": "<game mechanism>", "action": ...} — actions: board move pass setup; cards shuffle collect deal draw burn move play discard give reveal peek; pot fold check call bet raise all_in; slots place (guide("game"))
- `flow`: {"flow": "<flow mechanism>", "action": ...} — actions: procedure push pass counter; order extra_turn; victory — (guide("flow"))
- `operations`: {"operations": "<operations mechanism>", "action": ...} — actions: queue — (guide("operations"))
- `groups`: {"groups": "<groups mechanism>", "action": ...} — actions: roles eliminate reveal; relationships relate; factions join leave invite found ally break_alliance add remove; matching — (guide("groups"))
- `social`: {"social": "<social mechanism>", "action": ...} — actions: channels say dm reply broadcast read create_group invite join leave; diffusion step seed adopt reject expose; feed post reply repost react follow unfollow befriend unfriend block unblock mute unmute label (guide("social"))
- `mind`: {"mind": "<mind mechanism>", "action": ...} — actions: beliefs learn tell forget; personas write; memory note recall (guide("mind"))
- `conditions`: {"conditions": "<conditions mechanism>", "action": ...} — actions: status apply cleanse; cooldowns reset; channeling interrupt; terrain enter (guide("conditions"))
- `host`: {"host": "<host mechanism>", "action": ...} — actions: judge judge; game_master resolve; tool call; recap write (guide("host"))

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
`$best(items, [key1, key2])`; exact ties return a list by default, so use a unique
last key when the rule requires one deterministic item.

