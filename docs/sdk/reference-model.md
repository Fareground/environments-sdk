# model

## How a run works

Every round: `after` effects that are due → `host.feed` answers → `round.start` events → the `dynamics` step →
each stage in order → `round.end` events → series outputs sampled → invariants and end conditions checked. A run ends
when an `end` condition holds, an effect `end`s it, or `clock.rounds` is used up.

Events are the world's logic outside turns, one list: `on` is when an event is considered, `when` whether it
fires, `do` what it does (atomically), `say` the headline agents get as news, `once` at most once per run.
* `round.start` (the default) and `round.end`: every round. A schedule is a condition: `"$round == 5"`, `"$round in
  [1, 3]"`, `"$round % 7 == 1"` (every 7 rounds from round 1), `"$chance(0.1)"`, `"$arm == 'treatment'"`.
* `stage.<s>.start` when stage s starts (only if its `when` holds), `stage.<s>.end` after it (a simultaneous stage's
  choices have committed: resolve them here), `stage.<s>.turn` after each agent's turn in it, with `$actor`, `$acted`
  and `$timed_out` (a forfeit or default move: `"when": "not $acted"`).
* `create.<type>` / `remove.<type>`: inside the change that creates or removes an entity of the type (or a subtype;
  an ancestor's events first), with `$it` (in `remove`, already no longer alive), so a `fail` refuses that change.
  Entities made at build fire `create` once the whole world exists, in creation order (`$round` is 0 then).
* `change`: after every commit (an action, a sealed choice, an effect block, physics, the round's end), the moment
  `when` becomes true; it fires again only after it was false.
Events on one anchor fire in the order written, the author's before those mechanisms generate. A round event whose
`do` is one `each` loop runs item by item: each item has luck of its own, and invariants are checked once all ran.

A stage wakes agents (`who`, in `order`). An agent's turn is a short session: it reads its
brief + update, calls tools (legal actions, `look`, `inspect`, `end_turn`) until it ends the
turn, uses `max_actions` (default 1; on a stage mechanisms attach to, left out, the pool of what each allows, one
more for the stage's own actions), or runs out of `max_calls`.
* `turns: sequential` — one agent at a time; actions apply immediately and the tool result is
  the actual outcome.
* `turns: simultaneous` — everyone sees the same state; actions are submitted, then committed one agent
  after another once all have chosen (sealed bids, votes, simultaneous moves). Outcomes arrive as news.
  Without an `order`, choices commit in a random order drawn from the seed each time, so when two agents
  take the last item, either may get it. A choice is tried at submit after the agent's own earlier
  choices in the stage (two buys cannot spend the same coins).
* Choices that decide together (highest bid wins, pro-rata fills, rock–paper–scissors): the action only
  records the choice (`"announce": false, "do": ["$actor.bid = $params.amount"]`) and an event on
  `stage.<s>.end` resolves them all at once: `"$top = $max(bidder, $it.bid)"`, `"$winner = $choice($filter(bidder,
  $it.bid == $top))"` (ties at random); pro rata: `"$fill = $min(1, $world.stock / $max($sum(buyer, $it.want),
  1))"`, `{"each": "buyer", "do": ["$it.got = $it.want * $fill"]}`. Clear the recorded choices on
  `stage.<s>.start`.
* `until` repeats passes within the round (deliberation until everyone is ready).
* `quiet: skip` skips agents with nothing new since their last turn (from the second pass on;
  the first pass always wakes everyone).
* `look` and `inspect` are free reads: up to `max_calls` of them per turn use no call, and one past that is refused
  without spending a call, so an agent can always still act. The same read twice in a turn answers "Unchanged".
* A stage with `actions: []` wakes nobody: use it as a pure resolution step (events on its `start`/`end`).
* A stage without `actions` offers every action but a mechanism's tools attached (`stage:`) to another stage, which
  are that stage's own. When other stages list their own, list this stage's too
  (check warns otherwise: agents could take another phase's actions here), or write `"actions": "all"`.
* `must_act: true` removes `end_turn` while an action is available. An agent that could act and did not — in
  a `must_act` stage, or any stage once its calls ran out — is reported as an `idle` event ("Ben did not act.").
* `terminal` may be an expression checked after the action applies (`"$world.jump_finished"`), so a
  move can end the turn only sometimes (multi-jumps).
* `env.run(..., time_limit=30)` gives each agent wall-clock seconds for its turn. Past it the turn ends, later calls
  are refused, a `timeout` event is logged and the stage's `turn` events see `$timed_out`. The agent's update says
  how long it has. A participant that finishes in time plays exactly as it would without a limit.
* `valid` makes a turn's actions apply together: each applies at once (the agent sees its move), but events,
  reactions and invariants wait until the turn ends, and an action's own `outcome` text (and attached files) is
  shown once the turn commits — an undone turn shows nothing it was not charged for. Its conditions (`$actor`,
  `$pending`) are checked when a turn that acted ends, on the world its `change` events leave; if one fails, every action of the turn is undone, the agent is
  told `why` and plays the turn again (castling through check, a full backgammon move); `"valid": "true"` makes
  turns atomic with no condition. An action that draws randomness or reads a value hidden from its agent settles the
  turn so far at once, so later actions cannot undo its luck or what it revealed (if `valid` fails then, the turn is
  undone and over); a `valid` that draws or reads a hidden value and fails likewise undoes the turn and ends it,
  rather than letting the agent play it again. In a simultaneous stage each agent's
  choices commit or are undone together.
* Luck never decides whether a call is allowed: `when` requirements and parameters' bounds, defaults, values and
  `where` may not draw at random (a check error), since a refused call costs nothing and calling again would roll
  fresh luck. Draw in `do`: a call that drew has been played, even when a rule then fails. A view's
  randomness is fixed for the turn, so looking again shows the same noisy signal (and a preview shows the turn's).
* Views with `"for": "spectator"` are an omniscient picture for UIs and reports: rendered at the end of
  every round into `result.frames` (the last marked `final`) and on demand by `env.spectate()`, never
  shown to an agent. They have no `$actor`; randomness they draw never changes the run.
* Physics precedes agent stages. Coupled world and entity equations share intermediate integration states.
  Failed intervals restore equation state and property writebacks.
* Invariants are checked after every action and effect block (a round event's `each` once its last item ran, or
  before a `change` event or reaction an item sets off) and after physics: write them for states that must hold at all times, not
  ones that only settle at the end of a stage. `$all(<type>, <condition>)` whose condition reads only each member's
  own properties and `$inputs` re-checks only the members a change touched, so it stays cheap in any crowd. An
  agent's action that breaks one — itself or through the events its commit sets off — is refused and
  undone, and the agent is told the invariant's `why` (give one: without it the agent only hears that a rule would
  break; it is a template, which may read no agent's private prop); the run goes on and its diagnostics count it. A break by anything else (events, physics, the build) fails
  the run. `"check": "round"` checks one only at the end of every round (a conservation sum over a big crowd then
  costs one pass a round, not one per change) — a break found then fails the run, whatever caused it;
  `"check": "end"` once, when the run finishes.
* An agent's action is one undoable unit: the checks of its call (requirements, arguments), its effects, and the
  events its commit sets off. A rule that fails anywhere in it (a division by zero, a number too large) refuses and undoes that action
  alone — in a sealed stage when the choices commit, in an atomic turn the whole turn — and the agent is told the cause
  without the rule or any hidden value. The run goes on; `result.diagnostics` names the failing rule with a fix and
  `stats.faulted_actions` counts these refusals. Guard such rules (`min`/`max` on the parameter, or a `when` with a
  `why`) so agents are told the limit up front. What an action schedules with `after` stays that action's: when it
  runs, a refusal (a `fail`, a transfer or write that does not fit) or a failing rule in it undoes that block alone,
  the agent is told, and the run goes on. The same failure in events, world logic or physics fails the run, as
  do a host that fails and a crash in a mechanism's own code, wherever they happen.
* `end` conditions are checked after the start events, after each stage, and at the end of the round, so
  `$round == <clock.rounds>` ends the run before the last round plays (check warns): the run ends after its last
  round by itself; to name a winner then, use an `end` effect in a `round.end` event.
  `"check": "action"` also checks one the moment anything commits — an action, a sealed choice, an event's
  effects — so a winning move ends the run before the next agent moves (in any kind of stage; sealed
  choices commit one after another, so later ones are not applied). The `end` effect inside an action does the same.

What an agent reads:
* brief (static, cacheable): name, situation, rules, its identity and role text.
* update: time label and stage, "Your last turn: …" (what its actions of its previous turn returned — each that
  applied, then "Then: …" for a call refused after the last of them, or that the turn was undone and why — which a
  turn-ending action told it nothing of before), why it is acting ("Your turn again." only when it already had a turn
  in this stage this round), "Since your last turn" ("So far" on its first turn; announcements of
  others' actions, outcomes of its own simultaneous actions, record entries, event news; in a busy round what is
  addressed to it is always shown, then the newest news, then the newest of others' actions, and the rest counted),
  then every declared view that applies. Text written by participants is wrapped «like this».
* tools: one per legal action with a JSON Schema (entity choices as enums, numeric bounds when
  they depend only on the actor), plus look/inspect/end_turn. Invalid calls return what to fix. An action's name is
  its tool's name, so it must be one providers accept (letters, digits, _ and -, at most 64) and not a built-in's.

Unless an action sets `announce` (a template, or `false`: nobody else learns it happened), others read a default line
"Name: action (args)." — in a simultaneous stage only "Name: action." (sealed choices stay sealed
unless `announce` reveals them), and with no arguments at all when its effects may write a private property;
an action that posts to a record announces nothing extra (the entry is the news). Text an agent types (text params) keeps its provenance wherever it is stored and
always renders «quoted» on one line, in news, views and outcomes.

### Who sees what

An agent learns only what the contract shows it: `private` props, per-type views, record `visible` rules, `to` on
posts and emits, and `announce: false` on actions (nobody else learns they happened) say what each one sees. Agents get `inspect` only for types that set `inspect`.
A `private` prop is read by the entity itself, by its owner and by the agent types it lists — nobody else. An
entity's owner is a stated fact: its type names the prop holding the owner's id (`"owner": "seller"` on a listing
type; a list of ids names several owners), read from the entity's own value whenever it is shown, so handing a card
to another player hands over what it may read. A list of agent types in place of `true`
(`"private": ["chair"]`) also shows it to agents of those types — an area chair reading every review's score, an
auditor every ledger — and nothing widens it further: the author of a paper does not read the chair-only score of a
review of it unless the review's type makes that author its owner. The world's private props have no owner. A view's
or entity choice's `where` (or a policy rule's `each: $filter(<type>, <condition>)`) never grants anything: it picks
only among what its reader may read, so `"where": "$it.seller == $actor.id"` over a listing type with
`"owner": "seller"` lists the reader's own listings with their private props. A read of an item's private prop counts
as the reader's own only where a test that the item is its own must have held — after it in an `and`
(`"$it.seller == $actor.id and $it.floor > 3"`), after its negation in an `or`, in the branch of an `if` it decides
(`"$it.floor if $it.seller == $actor.id else 0"`), and for every item a `where` that picks the reader's own lets through;
one read anywhere else is an error, which `check` reports and `load` refuses.
Every field is read by someone, and that decides what a hidden value may do there — one rule per reader, which `check`
and the run both follow (`fg_env.contract.readers` lists every field):
* what one agent is shown or offered (views and their where/sort/attach, a brief, tool choices, bounds and defaults,
  outcome text, a refusal's why, whether an action ends the turn (`terminal`), its `attach`, a policy's rules, an inspect
  rule, a record's `show` and `visible`) may read that agent's own private props and nothing else hidden from it;
* what several agents are sent or learn from may read no private prop, not even the actor's: an `announce`, an event's
  or an `end`'s `say`, an invariant's `why`, an entity's `name` and `id` (as built and as `create` makes them), a stage's
  `when` and `order`, and a stage's `who`, `until` and `passes` when every agent learns whom it woke;
* what is sent (an emit's `say` and `data`, a post's fields, a wake's `why`) is read by whom it reaches: text `to` one
  agent may show only what that agent may read — the actor's private props only when it goes to the actor — and
  anything sent to several, or to everyone, may show no private prop;
* game logic (action `when`/`do`, events, `end` conditions, outputs, invariants) reads everything, and what it writes
  into what agents see is its reveal.
Reveal what an agent may learn by working it out in game logic (`"do": ["$seen = $params.target.role"], "outcome":
"... {$seen}"`, or a prop the agent owns), and what several may learn the same way (`"$shown = $actor.card"`, then
`{$shown}`).
A public fact about private data (how many cards a hand holds) is a public prop the rules keep up to date: write it
wherever the private one changes (`"$actor.cards = $len($actor.hand)"`).
The engine's own refusals (a transfer that does not fit, a bound) never show a hidden value. A `when` that reads a
hidden value does not hide the tool: it stays listed and a call is refused (and spent) when the `when` fails. A
non-agent entity reaches agents only through what the contract shows, so a prop the views already gate needs no
`private`. An entity's type is public (inspect names it): keep a secret role in a private prop, not a subtype. A refusal
is information too — a `when` or `fail` that reads hidden state tells the actor something about it. Visibility
shapes only what an agent is shown or offered (brief, updates, views, tool choices, outcome text, its policy); game logic — action
`when`/`do`, events, stages, `end`, outputs, invariants — reads every record entry and event,
so an auditor's `accuse` can count messages it never saw. To ask what one agent can see inside logic, filter
explicitly: `$records(chat, $it.author == $actor or $actor.id in ($it.to or []))`.

An action applies atomically: if any effect `fail`s or a `transfer` lacks funds, every change
is rolled back and the agent is told why. World logic (events) has no one to refuse: the same
failure there fails the run at its path, so guard such a block with an `if`. A refusal that rolled luck or whose rules
read a value hidden from the actor (a `when`, a `fail`, an error, a transfer, an update such as `-=` refused at a
bound) spends the action (a wrong guess at a
hidden code is a guess); any other refusal — a taken cell, bad arguments — costs nothing. Contract errors (bad expression at run time) stop
the run with status `failed` and the path of the broken rule.

