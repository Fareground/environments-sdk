# Cookbook

Each recipe is a small, complete contract for a pattern that comes up again and again. Start from the one nearest
your idea: `fg-env new <recipe> my_env.json` (`fg_env.new`) writes it, and you change the brief, the numbers and the
rules. Every recipe checks clean, plays with random agents, and gives the known answer shown under it with its coded
policy (`--agent policy:<name>` plays it for every agent).

| recipe | pattern |
|---|---|
| [`auction`](#auction) | sealed bids resolved together when the bidding ends |
| [`vote`](#vote) | talk in a shared record, then a secret ballot |
| [`negotiation`](#negotiation) | alternating offers with private limits and a deadline |
| [`hidden_roles`](#hidden_roles) | private roles, a secret night stage and open accusations |
| [`market`](#market) | posted prices, and buyers paying with a transfer |
| [`queue`](#queue) | arrivals served first come, first served |
| [`spread`](#spread) | an infection passing along a contact network (no agents) |
| [`board_game`](#board_game) | a turn-based board game with a winner and zero-sum seats |
| [`economy`](#economy) | gathering, eating and building with one action a day |
| [`grid`](#grid) | moving on a grid and harvesting a regrowing layer |
| [`simulation`](#simulation) | a population changing over time, measured every round (no agents) |

## auction

**Sealed-bid auction.** Sealed bids: each bid is recorded in a simultaneous stage with `announce: false`, and one event at the stage's end resolves them together: the highest bid wins the lot and pays its bid, a tie broken at random. For a second-price auction, charge the second-highest bid instead.

```json
{
  "fg_env": "2",
  "name": "Sealed-bid auction",
  "description": "Sealed bids: each bid is recorded in a simultaneous stage with `announce: false`, and one event at the stage's end resolves them together: the highest bid wins the lot and pays its bid, a tie broken at random. For a second-price auction, charge the second-highest bid instead.",
  "brief": {
    "situation": "One lot is auctioned each round. Each bidder knows only what the lot is worth to itself.",
    "rules": "Every round each bidder secretly bids at most its cash. The highest bid wins the lot and pays its bid; a tie is broken at random. Your profit is the lot's worth to you minus what you paid."
  },
  "inputs": {
    "values": {
      "type": "list",
      "default": [50, 40, 30],
      "description": "What a lot is worth to each bidder, one per bidder."
    },
    "lots": {"type": "int", "default": 3, "min": 1},
    "cash": {"type": "number", "default": 200, "min": 0, "description": "Each bidder's starting cash."}
  },
  "clock": {"rounds": "$inputs.lots", "unit": "lot"},
  "world": {"revenue": 0},
  "types": {
    "bidder": {
      "agent": true,
      "props": {
        "value": {"type": "number", "default": 0, "private": true},
        "bid": {"type": "number", "default": 0, "private": true},
        "cash": 0,
        "profit": 0,
        "won": 0
      },
      "policies": {"shade": {"rules": [{"do": "bid", "with": {"amount": "$floor($actor.value * 0.8)"}}]}},
      "score": {"value": "$it.profit"}
    }
  },
  "entities": {
    "bidder": {
      "type": "bidder",
      "count": "$len($inputs.values)",
      "props": {"value": "$inputs.values[$i - 1]", "cash": "$inputs.cash"}
    }
  },
  "actions": {
    "bid": {
      "by": "bidder",
      "description": "Bid for this round's lot.",
      "params": {"amount": {"type": "number", "min": 0, "max": "$actor.cash"}},
      "do": "$actor.bid = $params.amount",
      "announce": false,
      "outcome": "You bid {$params.amount|money}."
    }
  },
  "stages": [{"name": "bidding", "turns": "simultaneous"}],
  "views": {
    "me": {"for": "bidder", "show": "A lot is worth {value|money} to you. You have {cash|money}; profit so far {profit|money}."}
  },
  "events": [
    {
      "on": "stage.bidding.end",
      "do": [
        "$top = $max(bidder, $it.bid)",
        {
          "if": "$top > 0",
          "then": [
            "$winner = $choice($filter(bidder, $it.bid == $top))",
            "$winner.cash -= $top",
            "$winner.profit += $winner.value - $top",
            "$winner.won += 1",
            "$world.revenue += $top",
            {"emit": "sale", "say": "{$winner.name} wins lot {$round} for {$top|money}."}
          ]
        },
        {"each": "bidder", "do": "$it.bid = 0"}
      ]
    }
  ],
  "outputs": {
    "revenue": "$world.revenue",
    "lots_won": "$dict(bidder, $it.id, $it.won)",
    "profit": "$dict(bidder, $it.id, $it.profit)"
  },
  "invariants": [{"expr": "$all(bidder, $it.cash >= 0)", "why": "Nobody pays more than its cash."}]
}
```

Known answer: `fg-env new auction && fg-env run auction.json --agent policy:shade --seed 1` gives `revenue` 120, `lots_won` {"bidder_1": 3, "bidder_2": 0, "bidder_3": 0}: every bidder bids 80% of its worth (40, 32, 24), so bidder_1 wins all three lots at 40.

## vote

**Town vote.** Talk, then vote: a sequential stage where each neighbour posts to a shared record, then a simultaneous ballot whose sealed choices an event at the stage's end tallies (plurality, a tie broken at random). For ranked, approval or majority-with-quorum ballots use the `decision` mechanism (guide('decision.ballot')).

```json
{
  "fg_env": "2",
  "name": "Town vote",
  "description": "Talk, then vote: a sequential stage where each neighbour posts to a shared record, then a simultaneous ballot whose sealed choices an event at the stage's end tallies (plurality, a tie broken at random). For ranked, approval or majority-with-quorum ballots use the `decision` mechanism (guide('decision.ballot')).",
  "brief": {
    "situation": "Neighbours decide how to spend the town's small budget.",
    "rules": "Everyone speaks once, then everyone votes in secret for one option. The option with the most votes is built; a tie is broken at random."
  },
  "inputs": {
    "options": {"type": "list", "default": ["park", "library", "road"], "description": "What the town can build."}
  },
  "clock": {"rounds": 1, "unit": "meeting"},
  "world": {"result": ""},
  "types": {
    "neighbour": {
      "agent": true,
      "props": {
        "favourite": {"type": "text", "default": "", "private": true},
        "vote": {"type": "text", "default": "", "private": true}
      },
      "policies": {
        "sincere": {
          "rules": [
            {"do": "speak", "with": {"text": "I would like a {$actor.favourite}."}},
            {"do": "vote", "with": {"choice": "$actor.favourite"}}
          ]
        }
      }
    }
  },
  "entities": {
    "ana": {"type": "neighbour", "name": "Ana", "props": {"favourite": "park"}},
    "ben": {"type": "neighbour", "name": "Ben", "props": {"favourite": "park"}},
    "cai": {"type": "neighbour", "name": "Cai", "props": {"favourite": "library"}}
  },
  "records": {"chat": {"fields": {"text": "text"}, "show": "{author}: {text}"}},
  "actions": {
    "speak": {
      "by": "neighbour",
      "description": "Say what you think the town should build.",
      "params": {"text": {"type": "text", "max_len": 280}},
      "do": {"post": "chat", "text": "$params.text"}
    },
    "vote": {
      "by": "neighbour",
      "description": "Cast your secret vote.",
      "params": {"choice": {"type": "enum", "values": "$inputs.options"}},
      "do": "$actor.vote = $params.choice",
      "announce": false,
      "outcome": "You voted for {$params.choice}."
    }
  },
  "stages": [
    {"name": "talk", "actions": ["speak"]},
    {"name": "ballot", "turns": "simultaneous", "actions": ["vote"], "must_act": true}
  ],
  "views": {
    "you": {"for": "neighbour", "show": "You would most like a {favourite}."},
    "meeting": {"for": "neighbour", "title": "The meeting so far", "of": "chat", "show": "{author}: {text}", "empty": "Nobody has spoken yet."}
  },
  "events": [
    {
      "on": "stage.ballot.end",
      "do": [
        "$world.result = $best($inputs.options, $count(neighbour, $it.vote == $outer))",
        {"emit": "result", "say": "The town will build a {$world.result}."}
      ]
    }
  ],
  "outputs": {"result": "$world.result", "votes": "$tally($map(neighbour, $it.vote))"}
}
```

Known answer: `fg-env new vote && fg-env run vote.json --agent policy:sincere --seed 1` gives `result` "park", `votes` {"park": 2, "library": 1}: Ana and Ben want a park and Cai a library; everyone votes for what they want.

## negotiation

**Price negotiation.** Alternating offers: one shared offer on the table, a sequential stage where each side either answers it or makes a counter-offer, private limits, and a deadline. Accepting ends the run with an `end` effect; without a deal by the last round nobody trades.

```json
{
  "fg_env": "2",
  "name": "Price negotiation",
  "description": "Alternating offers: one shared offer on the table, a sequential stage where each side either answers it or makes a counter-offer, private limits, and a deadline. Accepting ends the run with an `end` effect; without a deal by the last round nobody trades.",
  "brief": {
    "situation": "A seller and a buyer haggle over one used car.",
    "rules": "Each round the seller, then the buyer, may accept the offer on the table (if the other side made it) or make a new offer. A deal ends the talks. After {$inputs.deadline} rounds without a deal, nobody trades.",
    "roles": {"seller": "Sell for as much as you can, never below your cost.", "buyer": "Buy for as little as you can, never above what the car is worth to you."}
  },
  "inputs": {
    "deadline": {"type": "int", "default": 5, "min": 1, "unit": "round"},
    "cost": {"type": "number", "default": 40, "min": 0, "description": "The seller's lowest price."},
    "worth": {"type": "number", "default": 80, "min": 0, "description": "What the car is worth to the buyer."}
  },
  "clock": {"rounds": "$inputs.deadline"},
  "world": {
    "offer": {"type": "number", "default": 0},
    "offered_by": "",
    "price": {"type": "number", "default": 0},
    "deal": false
  },
  "types": {
    "party": {"agent": true, "props": {"limit": {"type": "number", "default": 0, "private": true}}},
    "seller": {
      "extends": "party",
      "policies": {
        "concede": {
          "rules": [
            {"when": "$world.offered_by == buyer and $world.offer >= $actor.limit + 10", "do": "accept"},
            {"do": "offer", "with": {"price": "$max($actor.limit + 10, 90 - 10 * $round)"}}
          ]
        }
      }
    },
    "buyer": {
      "extends": "party",
      "policies": {
        "concede": {
          "rules": [
            {"when": "$world.offered_by == seller and $world.offer <= $actor.limit - 10", "do": "accept"},
            {"do": "offer", "with": {"price": "$min($actor.limit - 10, 20 + 10 * $round)"}}
          ]
        }
      }
    }
  },
  "entities": {
    "seller": {"type": "seller", "name": "Seller", "props": {"limit": "$inputs.cost"}},
    "buyer": {"type": "buyer", "name": "Buyer", "props": {"limit": "$inputs.worth"}}
  },
  "actions": {
    "offer": {
      "by": "party",
      "description": "Put a price on the table.",
      "params": {"price": {"type": "number", "min": 0}},
      "do": ["$world.offer = $params.price", "$world.offered_by = $actor.id"],
      "announce": "{$actor.name} offers {$params.price|money}."
    },
    "accept": {
      "by": "party",
      "description": "Accept the other side's offer.",
      "when": {"expr": "$world.offered_by != '' and $world.offered_by != $actor.id", "why": "There is no offer from the other side to accept."},
      "do": [
        "$world.price = $world.offer",
        "$world.deal = true",
        {"end": "deal", "say": "Deal at {$world.price|money}."}
      ]
    }
  },
  "stages": [{"name": "talks", "order": "seat"}],
  "views": {
    "table": {"for": "party", "show": "{'On the table: ' + $text($world.offer) + ' from ' + $world.offered_by if $world.offered_by else 'No offer yet.'} Your limit is {limit|money}."}
  },
  "outputs": {"deal": "$world.deal", "price": "$world.price if $world.deal else null"}
}
```

Known answer: `fg-env new negotiation && fg-env run negotiation.json --agent policy:concede --seed 1` gives `deal` true, `price` 70: the seller asks 80, the buyer offers 30, the seller comes down to 70, and the buyer accepts any price at least 10 below its worth of 80.

## hidden_roles

**Wolves in the village.** Hidden roles: each player's role is a `private` prop dealt from a shuffled list; the wolves' night attack is a sealed stage open only to them (`who`, with every action unannounced), the day is an open accusation, and a role is revealed only when its player leaves. The `groups` mechanism deals, reveals and eliminates for you (guide('groups.roles')).

```json
{
  "fg_env": "2",
  "name": "Wolves in the village",
  "description": "Hidden roles: each player's role is a `private` prop dealt from a shuffled list; the wolves' night attack is a sealed stage open only to them (`who`, with every action unannounced), the day is an open accusation, and a role is revealed only when its player leaves. The `groups` mechanism deals, reveals and eliminates for you (guide('groups.roles')).",
  "brief": {
    "situation": "A village hides {$inputs.wolves} wolf among its {$inputs.players} players.",
    "rules": "Each night the wolves secretly choose a player to attack; that player leaves the game. Each day everyone accuses one player; the most accused leaves (a tie is broken at random). A player's role is revealed when it leaves. The village wins when no wolf is left; the wolves win when they are as many as the villagers.",
    "roles": {"player": "Your role is in your view. Villagers: find the wolves. Wolves: stay hidden."}
  },
  "inputs": {"players": {"type": "int", "default": 5, "min": 3}, "wolves": {"type": "int", "default": 1, "min": 1}},
  "clock": {"rounds": "$inputs.players", "unit": "day"},
  "world": {
    "deck": {"type": "list", "default": "$shuffle($map($range($inputs.players), 'wolf' if $it < $inputs.wolves else 'villager'))", "private": true}
  },
  "types": {
    "player": {
      "agent": true,
      "props": {
        "role": {"type": "enum", "values": ["wolf", "villager"], "default": "villager", "private": true},
        "attacks": {"type": "int", "default": 0, "private": true},
        "accused": 0
      },
      "policies": {
        "first": {
          "rules": [
            {"do": "attack", "with": {"target": "$pick(player, $it.id != $actor.id)"}},
            {"do": "accuse", "with": {"target": "$pick(player, $it.id != $actor.id)"}}
          ]
        }
      }
    }
  },
  "entities": {"player": {"type": "player", "count": "$inputs.players", "props": {"role": "$world.deck[$i - 1]"}}},
  "actions": {
    "attack": {
      "by": "player",
      "description": "Wolves: choose tonight's victim.",
      "when": "$actor.role == wolf",
      "params": {"target": {"type": "entity", "of": "player", "where": "$it.id != $actor.id"}},
      "do": "$params.target.attacks += 1",
      "announce": false,
      "outcome": "You attack {$params.target.name}."
    },
    "accuse": {
      "by": "player",
      "description": "Accuse a player of being a wolf.",
      "params": {"target": {"type": "entity", "of": "player", "where": "$it.id != $actor.id"}},
      "do": "$params.target.accused += 1"
    }
  },
  "stages": [
    {"name": "night", "turns": "simultaneous", "actions": ["attack"], "who": "$it.role == wolf"},
    {
      "name": "day",
      "when": "$wolves_left > 0 and $wolves_left < $count(player) - $wolves_left",
      "turns": "simultaneous",
      "actions": ["accuse"]
    }
  ],
  "views": {
    "you": {"for": "player", "show": "You are a {role}."},
    "players": {"for": "player", "title": "Players still in", "of": "player", "show": "{name}"}
  },
  "events": [
    {
      "on": "stage.night.end",
      "when": "$any(player, $it.attacks > 0)",
      "do": [
        {"call": "leave", "with": {"who": "$best(player, $it.attacks)", "how": "was attacked in the night"}},
        {"each": "player", "do": "$it.attacks = 0"}
      ]
    },
    {
      "on": "stage.day.end",
      "when": "$any(player, $it.accused > 0)",
      "do": [
        {"call": "leave", "with": {"who": "$best(player, $it.accused)", "how": "was sent away by the village"}},
        {"each": "player", "do": "$it.accused = 0"}
      ]
    }
  ],
  "outputs": {"winner": "$result.winner", "players_left": "$count(player)"},
  "end": [
    {"name": "village_wins", "when": "$wolves_left == 0", "winner": "villagers", "say": "No wolf is left: the village wins."},
    {"name": "wolves_win", "when": "$wolves_left >= $count(player) - $wolves_left", "winner": "wolves", "say": "The wolves are as many as the villagers: the wolves win."}
  ],
  "defs": {
    "leave": {
      "args": ["who", "how"],
      "do": [
        "$role = $who.role",
        {"emit": "left", "say": "{$who.name} {$how}. They were a {$role}."},
        {"remove": "$who"}
      ]
    },
    "wolves_left": {"expr": "$count(player, $it.role == wolf)"}
  }
}
```

Try it: `fg-env new hidden_roles && fg-env run hidden_roles.json --agent policy:first --seed 1`.

## market

**Bread market.** Posted prices and trading: the seller sets a price in its own stage, then buyers, in random order, buy what they can afford; each purchase moves money with one `transfer`, which refuses a buyer who is short and never lets cash go negative. An invariant guards the books.

```json
{
  "fg_env": "2",
  "name": "Bread market",
  "description": "Posted prices and trading: the seller sets a price in its own stage, then buyers, in random order, buy what they can afford; each purchase moves money with one `transfer`, which refuses a buyer who is short and never lets cash go negative. An invariant guards the books.",
  "brief": {
    "situation": "A baker sells bread to households each day.",
    "rules": "Each morning the baker sets the price of a loaf. Then, in random order, each household may buy up to 3 loaves it can afford. A loaf costs the baker {$inputs.cost|money} to bake.",
    "roles": {"baker": "Earn as much as you can.", "household": "Feed your family on a budget."}
  },
  "inputs": {
    "days": {"type": "int", "default": 7, "min": 1, "unit": "day"},
    "households": {"type": "int", "default": 3, "min": 1},
    "cash": {"type": "number", "default": 20, "min": 0, "description": "Each household's starting cash."},
    "cost": {"type": "number", "default": 1, "min": 0, "description": "What one loaf costs the baker."}
  },
  "clock": {"rounds": "$inputs.days", "unit": "day"},
  "types": {
    "baker": {
      "agent": true,
      "props": {"cash": 0, "price": 2, "sold": 0},
      "policies": {"steady": {"rules": [{"do": "set_price", "with": {"price": 2}}]}}
    },
    "household": {
      "agent": true,
      "props": {"cash": 0, "loaves": 0},
      "policies": {"steady": {"rules": [{"do": "buy", "with": {"qty": 2}}]}}
    }
  },
  "entities": {
    "mo": {"type": "baker", "name": "Mo"},
    "household": {
      "type": "household",
      "count": "$inputs.households",
      "name": "Household {$i}",
      "props": {"cash": "$inputs.cash"}
    }
  },
  "actions": {
    "set_price": {
      "by": "baker",
      "description": "Set today's price per loaf.",
      "params": {"price": {"type": "number", "min": 0.5, "max": 10}},
      "do": "$actor.price = $params.price",
      "announce": "Bread costs {$params.price|money} today."
    },
    "buy": {
      "by": "household",
      "description": "Buy loaves at today's price.",
      "params": {"qty": {"type": "int", "min": 1, "max": "$min(3, $floor($actor.cash / $entity(mo).price))"}},
      "when": {"expr": "$actor.cash >= $entity(mo).price", "why": "You cannot afford a loaf."},
      "do": [
        {"transfer": "cash", "from": "$actor", "to": "mo", "amount": "$params.qty * $entity(mo).price"},
        "$entity(mo).cash -= $params.qty * $inputs.cost",
        "$entity(mo).sold += $params.qty",
        "$actor.loaves += $params.qty"
      ],
      "outcome": "You bought {$params.qty} loaves; {$actor.cash|money} left."
    }
  },
  "stages": [
    {"name": "morning", "actions": ["set_price"]},
    {"name": "shopping", "actions": ["buy"], "order": "random"}
  ],
  "views": {
    "shop": {"for": "household", "show": "Bread costs {$entity(mo).price|money}. You have {cash|money} and {loaves} loaves."},
    "books": {"for": "baker", "show": "Cash {cash|money}; {sold} loaves sold at {price|money}."}
  },
  "outputs": {
    "profit": {"expr": "$entity(mo).cash", "type": "number", "format": "money"},
    "loaves_sold": {"expr": "$entity(mo).sold", "type": "int", "series": true},
    "price": {"expr": "$entity(mo).price", "type": "number", "series": true}
  },
  "invariants": [{"expr": "$all(household, $it.cash >= 0)", "why": "A household cannot spend money it does not have."}]
}
```

Known answer: `fg-env new market && fg-env run market.json --agent policy:steady --seed 1` gives `profit` 30, `loaves_sold` 30: each household buys 2 loaves a day at 2 until its 20 runs out after 5 days: 30 loaves, each earning 2 - 1.

## queue

**Service desk.** A queue served first come, first served: customers arrive as entities each round (from a demand list, the last value repeating), a manager staffs the desk, and an event serves the oldest customers up to capacity with `$sort`, adding up each one's wait. Measures cost, customers served and the mean wait.

```json
{
  "fg_env": "2",
  "name": "Service desk",
  "description": "A queue served first come, first served: customers arrive as entities each round (from a demand list, the last value repeating), a manager staffs the desk, and an event serves the oldest customers up to capacity with `$sort`, adding up each one's wait. Measures cost, customers served and the mean wait.",
  "brief": {
    "situation": "Customers queue at a service desk.",
    "rules": "Each round new customers arrive and join the back of the queue. The manager opens 1 to {$inputs.max_servers} counters; each serves {$inputs.per_server} customers a round, oldest first, and costs {$inputs.wage|money} a round. Keep both the waiting and the cost low.",
    "roles": {"manager": "Balance the customers' waiting against the cost of open counters."}
  },
  "inputs": {
    "arrivals": {
      "type": "list",
      "default": [4, 6, 8, 5],
      "description": "Customers arriving each round; the last value repeats."
    },
    "rounds": {"type": "int", "default": 4, "min": 1},
    "per_server": {"type": "int", "default": 3, "min": 1},
    "max_servers": {"type": "int", "default": 5, "min": 1},
    "wage": {"type": "number", "default": 10, "min": 0}
  },
  "clock": {"rounds": "$inputs.rounds"},
  "world": {"servers": 1, "served": 0, "waited": 0, "cost": 0},
  "types": {
    "manager": {"agent": true, "policies": {"two": {"rules": [{"do": "staff", "with": {"counters": 2}}]}}},
    "customer": {"props": {"arrived": 0}}
  },
  "entities": {"boss": {"type": "manager", "name": "Manager"}},
  "actions": {
    "staff": {
      "by": "manager",
      "description": "Choose how many counters are open this round.",
      "params": {"counters": {"type": "int", "min": 1, "max": "$inputs.max_servers"}},
      "do": "$world.servers = $params.counters",
      "announce": "{$params.counters} counter(s) open."
    }
  },
  "stages": [{"name": "staffing"}],
  "views": {
    "desk": {"for": "manager", "show": "{$count(customer)} customers are waiting; {$world.served} served so far at a cost of {$world.cost|money}."}
  },
  "events": [
    {
      "name": "arrivals",
      "do": {
        "create": "customer",
        "count": "$get($inputs.arrivals, $round - 1, $last($inputs.arrivals))",
        "props": {"arrived": "$round"}
      }
    },
    {
      "name": "service",
      "on": "round.end",
      "do": [
        "$world.cost += $world.servers * $inputs.wage",
        {
          "each": "$sort(customer, $it.arrived, $world.servers * $inputs.per_server)",
          "do": ["$world.waited += $round - $it.arrived", "$world.served += 1", {"remove": "$it"}]
        }
      ]
    }
  ],
  "outputs": {
    "served": "$world.served",
    "waiting": {"expr": "$count(customer)", "type": "int", "series": true},
    "mean_wait": {"expr": "$world.waited / $world.served if $world.served else 0", "type": "number", "unit": "round"},
    "cost": {"expr": "$world.cost", "type": "number", "format": "money"}
  }
}
```

Known answer: `fg-env new queue && fg-env run queue.json --agent policy:two --seed 1` gives `served` 22, `cost` 80: 2 counters serve 6 a round: 4 + 6 + 6 + 6 of the 23 arrivals; 2 counters × 10 × 4 rounds.

## spread

**Outbreak on a network.** Spreading on a network: people linked in a small-world contact graph, an infection that passes along links with a set chance per sick contact, and recovery after a few days. One `each` loop with `sync: true` updates everyone from the same picture of yesterday. No agents: a simulation measured over time with series outputs.

```json
{
  "fg_env": "2",
  "name": "Outbreak on a network",
  "description": "Spreading on a network: people linked in a small-world contact graph, an infection that passes along links with a set chance per sick contact, and recovery after a few days. One `each` loop with `sync: true` updates everyone from the same picture of yesterday. No agents: a simulation measured over time with series outputs.",
  "brief": {
    "situation": "An infection spreads through a town's contact network.",
    "rules": "Each day a healthy person catches it from each sick contact with the given chance; the sick recover after a few days and cannot catch it again."
  },
  "inputs": {
    "people": {"type": "int", "default": 60, "min": 2},
    "contacts": {"type": "int", "default": 4, "min": 2, "description": "Mean contacts per person."},
    "infectivity": {"type": "number", "default": 0.25, "min": 0, "max": 1, "description": "Chance a sick contact passes it on in a day."},
    "sick_days": {"type": "int", "default": 4, "min": 1},
    "days": {"type": "int", "default": 30, "min": 1, "unit": "day"}
  },
  "clock": {"rounds": "$inputs.days", "unit": "day"},
  "types": {
    "person": {
      "props": {
        "state": {"type": "enum", "values": ["healthy", "sick", "recovered"], "default": "healthy"},
        "sick_for": 0
      }
    }
  },
  "entities": {
    "person": {"type": "person", "count": "$inputs.people", "props": {"state": "sick if $i == 1 else healthy"}}
  },
  "relations": {
    "contact": {
      "symmetric": true,
      "links": [{"among": "person", "graph": "small_world", "degree": "$inputs.contacts", "p": 0.1}]
    }
  },
  "events": [
    {
      "name": "day",
      "do": {
        "each": "person",
        "sync": true,
        "do": [
          {
            "if": "$it.state == sick",
            "then": ["$it.sick_for += 1", {"if": "$it.sick_for >= $inputs.sick_days", "then": "$it.state = recovered"}],
            "else": [
              {"if": "$it.state == healthy and $chance(1 - (1 - $inputs.infectivity) ** $count($neighbors($it, contact), $it.state == sick))", "then": "$it.state = sick"}
            ]
          }
        ]
      }
    }
  ],
  "outputs": {
    "sick": {"expr": "$count(person, $it.state == sick)", "type": "int", "series": true},
    "ever_sick": {"expr": "$count(person, $it.state != healthy)", "type": "int"},
    "peak_sick": {"expr": "$max($series.sick) or 0", "type": "int"}
  },
  "end": [{"name": "over", "when": "$count(person, $it.state == sick) == 0", "say": "Nobody is sick any more."}]
}
```

Try it: `fg-env new spread && fg-env run spread.json --seed 1`.

## board_game

**Tic-tac-toe.** A turn-based board game: the board is a world list, a move's legality is a requirement on its parameter, and the move that completes a line ends the game at once with an `end` effect naming the winner. Each seat scores 1, 0 or -1 (`score`, zero-sum), so tournaments, game search and gyms can play it.

```json
{
  "fg_env": "2",
  "name": "Tic-tac-toe",
  "description": "A turn-based board game: the board is a world list, a move's legality is a requirement on its parameter, and the move that completes a line ends the game at once with an `end` effect naming the winner. Each seat scores 1, 0 or -1 (`score`, zero-sum), so tournaments, game search and gyms can play it.",
  "brief": {
    "situation": "Two players take turns on a 3 by 3 board, X first.",
    "rules": "On your turn mark one empty cell (0 to 8, row by row). Three of your marks in a row, column or diagonal wins; a full board with no line is a draw."
  },
  "clock": {"rounds": 5, "unit": "turn"},
  "world": {"board": {"type": "list", "default": ["", "", "", "", "", "", "", "", ""]}},
  "types": {
    "player": {
      "agent": true,
      "props": {"mark": ""},
      "policies": {
        "first_free": {"rules": [{"do": "place", "with": {"cell": "$pick($range(9), $world.board[$it] == '')"}}]}
      },
      "score": {"value": "0 if $result.winner == null else 1 if $result.winner == $it else -1", "utility": "zero_sum"}
    }
  },
  "entities": {
    "x": {"type": "player", "name": "X", "props": {"mark": "X"}},
    "o": {"type": "player", "name": "O", "props": {"mark": "O"}}
  },
  "actions": {
    "place": {
      "by": "player",
      "description": "Mark an empty cell.",
      "params": {"cell": {"type": "int", "min": 0, "max": 8}},
      "when": {"expr": "$world.board[$params.cell] == ''", "why": "That cell is taken."},
      "do": [
        "$world.board[$params.cell] = $actor.mark",
        {
          "if": "$line($actor.mark)",
          "then": {"end": "line", "winner": "$actor", "say": "{$actor.name} completes a line and wins."}
        },
        {"if": "$count($world.board, $it == '') == 0", "then": {"end": "draw", "say": "The board is full: a draw."}}
      ],
      "announce": "{$actor.name} marks cell {$params.cell}."
    }
  },
  "stages": [{"name": "play", "must_act": true}],
  "views": {
    "board": {"for": "player", "show": "You play {mark}. Board, row by row: {$join($map($range(9), $world.board[$it] or $text($it)), ' ')} (numbers are empty cells)."}
  },
  "outputs": {"winner": "$result.winner.name if $result.winner else 'draw'"},
  "defs": {
    "line": {
      "args": ["m"],
      "expr": "$any([[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]], $world.board[$it[0]] == $m and $world.board[$it[1]] == $m and $world.board[$it[2]] == $m)",
      "description": "Whether mark m holds a whole line."
    }
  }
}
```

Known answer: `fg-env new board_game && fg-env run board_game.json --agent policy:first_free --seed 1` gives `winner` "X": both mark the lowest free cell, so X holds 0, 2, 4 and 6 and wins on the diagonal.

## economy

**Frontier village.** A resource economy: each turn a settler gathers food or wood, or builds a house from wood; everyone eats every evening, and a settler who cannot eat leaves. Production, consumption and investment compete for the same turns; houses built is the score.

```json
{
  "fg_env": "2",
  "name": "Frontier village",
  "description": "A resource economy: each turn a settler gathers food or wood, or builds a house from wood; everyone eats every evening, and a settler who cannot eat leaves. Production, consumption and investment compete for the same turns; houses built is the score.",
  "brief": {
    "situation": "Settlers must feed themselves while building a village.",
    "rules": "Each day you take one action: gather {$inputs.food_yield} food, gather {$inputs.wood_yield} wood, or build a house for {$inputs.house_cost} wood. Every evening you eat 1 food; if you have none, you leave the village."
  },
  "inputs": {
    "settlers": {"type": "int", "default": 3, "min": 1},
    "days": {"type": "int", "default": 8, "min": 1, "unit": "day"},
    "food_yield": {"type": "int", "default": 2, "min": 1},
    "wood_yield": {"type": "int", "default": 2, "min": 1},
    "house_cost": {"type": "int", "default": 5, "min": 1}
  },
  "clock": {"rounds": "$inputs.days", "unit": "day"},
  "types": {
    "settler": {
      "agent": true,
      "props": {"food": 2, "wood": 0, "houses": 0},
      "policies": {
        "balanced": {
          "rules": [
            {"do": "build"},
            {"when": "$actor.food < 2", "do": "gather", "with": {"resource": "food"}},
            {"do": "gather", "with": {"resource": "wood"}}
          ]
        }
      },
      "score": {"value": "$it.houses"}
    }
  },
  "entities": {"settler": {"type": "settler", "count": "$inputs.settlers"}},
  "actions": {
    "gather": {
      "by": "settler",
      "description": "Gather food or wood.",
      "params": {"resource": {"type": "enum", "values": ["food", "wood"]}},
      "do": [
        {"if": "$params.resource == food", "then": "$actor.food += $inputs.food_yield", "else": "$actor.wood += $inputs.wood_yield"}
      ],
      "outcome": "You have {$actor.food} food and {$actor.wood} wood."
    },
    "build": {
      "by": "settler",
      "description": "Build a house from wood.",
      "when": {"expr": "$actor.wood >= $inputs.house_cost", "why": "You need {$inputs.house_cost} wood."},
      "do": ["$actor.wood -= $inputs.house_cost", "$actor.houses += 1"],
      "announce": "{$actor.name} builds a house."
    }
  },
  "stages": [{"name": "work", "must_act": true}],
  "views": {"stores": {"for": "settler", "show": "You have {food} food, {wood} wood and {houses} house(s)."}},
  "events": [
    {
      "name": "supper",
      "on": "round.end",
      "do": {
        "each": "settler",
        "do": [
          {
            "if": "$it.food > 0",
            "then": "$it.food -= 1",
            "else": [{"emit": "left", "say": "{$it.name} had nothing to eat and left."}, {"remove": "$it"}]
          }
        ]
      }
    }
  ],
  "outputs": {
    "houses": {"expr": "$sum(settler, $it.houses)", "type": "int", "series": true},
    "settlers_left": {"expr": "$count(settler)", "type": "int"}
  }
}
```

Known answer: `fg-env new economy && fg-env run economy.json --agent policy:balanced --seed 1` gives `houses` 3, `settlers_left` 3: gathering food whenever it has less than 2, each settler has 6 wood after day 5 and builds a house on day 6.

## grid

**Foragers on a grid.** Movement in space: foragers on a grid step up, down, left or right to a free cell and eat the berries there; berries are a layer, a value on every cell, that regrows each round. A def turns a direction into a cell, one requirement keeps moves on the board and off other foragers, and the space's capacity holds one forager per cell.

```json
{
  "fg_env": "2",
  "name": "Foragers on a grid",
  "description": "Movement in space: foragers on a grid step up, down, left or right to a free cell and eat the berries there; berries are a layer, a value on every cell, that regrows each round. A def turns a direction into a cell, one requirement keeps moves on the board and off other foragers, and the space's capacity holds one forager per cell.",
  "brief": {
    "situation": "Foragers roam a {$inputs.size} by {$inputs.size} meadow of berry bushes.",
    "rules": "Each round step up, down, left or right to a free cell and eat every berry there. Each cell regrows one berry a round, up to 3."
  },
  "inputs": {
    "size": {"type": "int", "default": 5, "min": 2},
    "foragers": {"type": "int", "default": 2, "min": 1},
    "rounds": {"type": "int", "default": 6, "min": 1}
  },
  "clock": {"rounds": "$inputs.rounds"},
  "space": {
    "grid": {"rows": "$inputs.size", "cols": "$inputs.size"},
    "capacity": 1,
    "layers": {"berries": {"type": "int", "default": 2, "min": 0, "max": 3}}
  },
  "types": {
    "forager": {
      "agent": true,
      "props": {"eaten": 0},
      "policies": {
        "greedy": {
          "rules": [
            {
              "do": "move",
              "with": {"direction": "$best($filter(['up', 'down', 'left', 'right'], $free($step($actor, $it))), $layer(berries, $step($actor, $it)))"}
            }
          ]
        }
      },
      "score": {"value": "$it.eaten"}
    }
  },
  "entities": {"forager": {"type": "forager", "count": "$inputs.foragers", "at": "$random_empty()"}},
  "actions": {
    "move": {
      "by": "forager",
      "description": "Step to a free neighbouring cell and eat its berries.",
      "params": {"direction": {"type": "enum", "values": ["up", "down", "left", "right"]}},
      "when": {"expr": "$free($step($actor, $params.direction))", "why": "That way is off the meadow or taken."},
      "do": [
        {"move": "$actor", "to": "$step($actor, $params.direction)"},
        "$ate = $layer(berries, $actor)",
        "$actor.eaten += $ate",
        {"layer": "berries", "at": "$actor", "set": 0}
      ],
      "outcome": "You ate {$ate} berries."
    }
  },
  "stages": [{"name": "forage", "order": "random"}],
  "views": {
    "here": {"for": "forager", "show": "You are at {at} and have eaten {eaten} berries."},
    "around": {"for": "forager", "title": "One step away", "of": "$filter(['up', 'down', 'left', 'right'], $free($step($actor, $it)))", "show": "{$it}: {$layer(berries, $step($actor, $it))} berries", "empty": "Every way is blocked."}
  },
  "events": [{"name": "regrow", "on": "round.end", "do": {"layer": "berries", "set": "$min($value + 1, 3)"}}],
  "outputs": {
    "eaten": "$dict(forager, $it.id, $it.eaten)",
    "berries_left": {"expr": "$sum($cells(), $layer(berries, $it))", "type": "int", "series": true}
  },
  "defs": {
    "step": {
      "args": ["who", "direction"],
      "expr": "[$who.at[0] + $get({up: -1, down: 1}, $direction, 0), $who.at[1] + $get({left: -1, right: 1}, $direction, 0)]",
      "description": "The cell one step from who in a direction."
    },
    "free": {
      "args": ["cell"],
      "expr": "$cell[0] >= 0 and $cell[0] < $inputs.size and $cell[1] >= 0 and $cell[1] < $inputs.size and $len($at($cell)) == 0",
      "description": "Whether a cell is on the board and empty."
    }
  }
}
```

Try it: `fg-env new grid && fg-env run grid.json --agent policy:greedy --seed 1`.

## simulation

**Wealth exchange.** A simulation over time with no agents: every round each person with money gives one unit to someone chosen at random. Equal starting wealth turns into strong inequality; series outputs record the Gini coefficient each round and an invariant checks that money is conserved.

```json
{
  "fg_env": "2",
  "name": "Wealth exchange",
  "description": "A simulation over time with no agents: every round each person with money gives one unit to someone chosen at random. Equal starting wealth turns into strong inequality; series outputs record the Gini coefficient each round and an invariant checks that money is conserved.",
  "brief": {
    "situation": "People start with the same wealth and trade at random.",
    "rules": "Every round each person with money gives one unit to another person chosen at random."
  },
  "inputs": {
    "people": {"type": "int", "default": 50, "min": 2},
    "wealth": {"type": "int", "default": 5, "min": 0, "description": "Everyone's starting wealth."},
    "rounds": {"type": "int", "default": 30, "min": 1}
  },
  "clock": {"rounds": "$inputs.rounds"},
  "types": {"person": {"props": {"wealth": {"type": "int", "default": 0, "min": 0}}}},
  "entities": {"person": {"type": "person", "count": "$inputs.people", "props": {"wealth": "$inputs.wealth"}}},
  "events": [
    {
      "name": "exchange",
      "do": {
        "each": "person",
        "where": "$giver.wealth > 0",
        "as": "giver",
        "do": [
          {"transfer": "wealth", "from": "$giver", "to": "$choice($filter(person, $it.id != $giver.id))", "amount": 1}
        ]
      }
    }
  ],
  "outputs": {
    "gini": {"expr": "$gini(person, $it.wealth)", "type": "number", "series": true},
    "broke": {"expr": "$count(person, $it.wealth == 0)", "type": "int", "series": true},
    "richest": {"expr": "$max(person, $it.wealth)", "type": "int"}
  },
  "invariants": [
    {"expr": "$sum(person, $it.wealth) == $inputs.wealth * $inputs.people", "why": "Money is only passed on, never made or lost.", "check": "round"}
  ]
}
```

Try it: `fg-env new simulation && fg-env run simulation.json --seed 1`.
