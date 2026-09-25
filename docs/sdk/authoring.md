# fg_env — build an environment

An environment is one JSON contract: data, not code. It says what exists, what agents can do, when they act, what
each one sees and what is measured; the engine runs it, reproducibly under a seed. Each turn an agent reads a brief
and an update and gets one typed tool per action it can take right now.

**Faithful first, configurable second.** Implement every requirement the brief states, exactly as stated: its
numbers, rules, timing, who sees what, and every output under the name it gives. Then make values configurable: an
input whose default *is* the brief's value. Keep every stated requirement while fixing check issues.

## The loop: write → check → preview → run

1. Write the contract, or start from the cookbook recipe nearest your idea: `fg-env new auction my_env.json`
   (`guide('cookbook')` lists them).
2. `fg-env check lake.json` (`fg_env.check`): static checks, then short plays with random, idle and coded agents.
   Every issue names its path and a fix.
3. `fg-env preview lake.json fisher_1` (`env.preview(id)`): exactly what that agent reads and which tools it gets.
   Each role should see what the brief says, nothing more.
4. `fg-env run lake.json --seed 1` (`fg_env.run`): compare the outputs with an answer worked out by hand for a small
   case. A clean check means the rules are consistent and short plays did not fail; it proves neither that every
   choice agents can make works nor that the environment is right.

## Worked example

Brief: *Three fishers share a lake of 100 fish for five seasons. Each season every fisher secretly decides how many
fish to catch, 0 to 10. After the catch the lake regrows by 20%, never above 100. Fishers see the lake and their own
total catch. Report `catch_by_fisher` (fisher id → total catch) and `fish_left`.*

```json
{
  "name": "Shared lake",
  "brief": {"rules": "You share a lake. Each season everyone secretly picks a catch; then the lake regrows by {$inputs.regrowth|pct}, up to {$inputs.capacity} fish."},
  "clock": {"rounds": "$inputs.seasons", "unit": "season"},
  "inputs": {
    "fishers": {"type": "int", "default": 3, "min": 1},
    "seasons": {"type": "int", "default": 5, "min": 1},
    "capacity": {"type": "int", "default": 100, "min": 0},
    "max_catch": {"type": "int", "default": 10, "min": 0},
    "regrowth": {"type": "number", "default": 0.2, "min": 0}
  },
  "world": {"fish": "$inputs.capacity"},
  "types": {"fisher": {"agent": true, "props": {"caught": 0, "asked": 0}}},
  "entities": {"fisher": {"type": "fisher", "count": "$inputs.fishers"}},
  "stages": [{"name": "fish", "turns": "simultaneous"}],
  "actions": {"catch": {
    "by": "fisher", "description": "Ask for fish from the lake this season.",
    "params": {"amount": {"type": "int", "min": 0, "max": "$inputs.max_catch"}},
    "do": "$actor.asked = $params.amount", "announce": false,
    "outcome": "You asked for {$params.amount} fish."
  }},
  "events": [
    {"on": "stage.fish.end", "do": [
      "$share = $min(1, $world.fish / $max(1, $sum(fisher, $it.asked)))",
      {"each": "fisher", "do": ["$got = $floor($it.asked * $share)", "$world.fish -= $got", "$it.caught += $got",
                                "$it.asked = 0"]}
    ]},
    {"on": "round.end", "do": "$world.fish = $min($inputs.capacity, $floor($world.fish * (1 + $inputs.regrowth)))"}
  ],
  "views": {"lake": {"show": "The lake holds {$world.fish} fish. You have caught {caught} in all."}},
  "outputs": {"catch_by_fisher": "$dict(fisher, $it.id, $it.caught)", "fish_left": "$world.fish"},
  "invariants": [{"expr": "$world.fish >= 0 and $world.fish <= $inputs.capacity", "why": "The lake holds 0 to capacity fish."}]
}
```

Every number in the brief is an input defaulting to it. "Secretly" is a simultaneous stage whose choices an event at
its end shares out, so identical asks get identical catches; regrowth is a `round.end` event. Save it as
`lake.json` and test a case worked out from the brief:

```python
import fg_env

assert fg_env.check("lake.json") == []
print(fg_env.load("lake.json").preview("fisher_1"))

def greedy(wake):  # asks 10 a season; wake.me: own props, wake.update: what an LLM reads
    wake.call("catch", {"amount": 10})
    wake.end()

# By hand: 100 → 70 → 84 → 54 → 64 → 34 → 40 → 10 → 12 → 0 fish; the last 12 are shared: 4 × 10 + 4 = 44 each.
result = fg_env.run("lake.json", greedy, seed=1)
assert result.ok and result.outputs["fish_left"] == 0
assert result.outputs["catch_by_fisher"] == {"fisher_1": 44, "fisher_2": 44, "fisher_3": 44}
```

## The ten concepts

Every section is optional except `name` and `types`; `guide('<section>')` has each one's fields.

1. **Parameters** — `inputs`: `{name: {type, default, min, max, values, description}}`, read as `$inputs.name`
   and set per run (`fg_env.run(..., inputs={...})`). Types: number int bool text enum list map table file.
2. **State** — `world` holds global props (`$world.fish`); `types` declare each kind's props (a bare value is the
   default: `"cash": 100`, any number; `"type": "int"` for whole ones — or `{type, default, min, max, values,
   private}`); `entities` are named
   (`"ann": {"type": "buyer"}`) or generated (`count`, or `from` an input table, one per `$row`: ids
   `<key>_1` …); `records` are logs (`{chat: {fields, show, visible}}`) that the `post` effect appends to.
3. **Actors** — a type with `"agent": true` takes turns. Its `policies` are coded participants
   (`policy:<name>`: first rule whose condition holds and action is legal) for crowds and baselines; `policy` names
   the default. `extends` inherits another type's props and role.
4. **Actions** — `actions`: `{act: {by, description, params, when, do, outcome, announce, terminal}}`, one tool each.
   `params`: `{p: {type, min, max, values, of, where}}`; an `entity` param names its type in `of` and filters with
   `where` (`$it` the candidate). `when` holds requirements: `["$actor.cash > 0", {"expr": "$params.qty <=
   $world.stock", "why": "Not enough stock."}]`; those over `$actor` decide whether the tool is offered, those
   reading `$params` refuse a call with their `why`. An action is atomic: a `fail` or a short `transfer` undoes it.
5. **Turns** — `clock` (`{rounds, unit}`) and `stages`, run in order every round:
   `[{name, when, actions, turns, who, order, max_actions, until, valid}]`. `turns: sequential` (the default) is
   one agent at a time, each action applied at once; `turns: simultaneous` lets everyone choose from the same
   picture (sealed bids, votes), so resolve the choices in an event on `stage.<s>.end`. A turn ends after
   `max_actions` actions (default 1) or when the agent ends it.
6. **Happenings** — `events`: `[{name, on, when, do, say, once}]`, the world's logic outside turns. `on` is when it
   is considered: `round.start` (default), `round.end`, `stage.<s>.start`, `stage.<s>.end`, `stage.<s>.turn` (with
   `$actor` and `$acted`: a default move is `"when": "not $acted"`), `create.<type>`, `remove.<type>`, or `change`.
   `when` is the condition: `"$round == 5"`, `"$round % 7 == 1"`, `"$chance(0.1)"`.
7. **Information** — `brief` (`{situation, rules, roles: {type: text}}`) is read first; `views`
   (`{v: {for, title, of, where, sort, desc, limit, show}}`) are read every turn: `of` omitted is one line about
   `$actor`, and a list includes the viewer unless `where: "$it.id != $actor.id"`. A `private` prop is read only by its
   entity, its type's `owner` and the types it lists; a record's `visible` says who reads each entry; `announce` is
   the line others read when an action happens (`false`: nobody learns of it).
8. **Outcomes** — `outputs`: `{name: expr}` or `{name: {expr, type, series}}`; `series: true` samples it every round
   (`$series.name`; `result.series`). `end`: `[{when, winner, say}]` stops the run early. `invariants`: `[expr or
   {expr, why}]` must always hold; an action that breaks one is refused. A player type's `score` names what each seat earns.
9. **Randomness** — no section: `$chance(0.3)`, `$randint(1, 6)`, `$normal(0, 1)`, `$choice(list)` draw from
   the run's seed, so a seed replays the run exactly.
10. **Reuse** — `defs` name an expression (`$utility(...)`) or an effect list (`{"call": name, "with": {...}}`);
   `imports` merge other files; `mechanisms` (`{name: {kind, mode, ...}}`) are ready-made rules for markets,
   ballots, hidden roles, queues, cards, dynamics and more that expand into ordinary sections
   (`guide('mechanisms')`; `fg-env expand --mechanisms` shows what they add). Also extended: `space`, `relations`,
   `arms`.

A round: `round.start` events → each stage in order → `round.end` events → series sampled → `end` checked. A run
ends on an `end` condition or effect, or when its rounds run out.

## Expressions and effects

A string with `$name` in it is an expression; other strings are text.
* Roots: `$actor` (who acts), `$params` (its arguments), `$it` (the current item), `$world`, `$inputs`, `$round`,
  `$outputs`, and locals you assign (`$total`). Props: `$actor.coins`, `$params.target.name`, `$entity(shop).stock`;
  every entity also has `id name type alive`.
* Operators: `+ - * / // % **`, `== != < <= > >=`, `and or not`, `in`, `a if cond else b`, lists `[1, 2]`, maps
  `{price: 3}`. Bare words are text: `$actor.role == wolf`.
* Functions take `$`: `$count(buyer, $it.cash > 0)`, `$sum(player, $it.coins)`, `$avg`, `$min`, `$max`,
  `$filter(player, $it.alive)`, `$map(player, $it.name)`, `$dict`,
  `$top`, `$sort`, `$best`, `$any`, `$all`, `$len`, `$get(list, i, 0)`,
  `$records`, `$round(x, 2)`, `$floor`, `$clamp`. `$min` `$max` `$sum` `$avg` take a collection and a value
  (`$min(stand, $it.price)`) or a list; `$min` and `$max` also take numbers (`$min(3, $x)`).
* Templates (`show`, `outcome`, `announce`, `say`, `brief`, `name`): `"{name} has {coins} coins"` reads the subject
  (`$it` in lists, `$actor` otherwise); `{$params.amount|money}` is any expression with a format.

`do` is one effect or a list:
* `"$actor.coins -= $params.amount"`; `"$total = $params.qty * 2"` sets a local, which lasts to the end of that
  `do` (nested effects and `outcome` too), never into the next action or event: keep such state in a prop.
* `{"if": "...", "then": [...], "else": [...]}` · `{"each": "player", "where": "...", "do": [...]}`
* `{"transfer": "coins", "from": "$actor", "to": "$params.target", "amount": 3}` (fails if short)
* `{"create": "order", "props": {...}}` · `{"remove": "$params.order"}` · `{"post": "chat", "text": "..."}`
* `{"fail": "Not enough stock."}` · `{"end": "bankrupt", "winner": "$actor", "say": "..."}`

## Read next, only when you need it

`guide('cookbook')`: complete contracts for common patterns (auctions, votes, negotiation, hidden roles, markets,
queues, networks, board games, economies, simulations). Then a section's fields (`guide('actions')` …), `effects`,
`functions`, `mechanisms`, `model` (a run in detail), `running` (Python). `guide()` maps every part.

