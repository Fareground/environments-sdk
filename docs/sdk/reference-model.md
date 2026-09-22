# model

## How a run works

On a rounds clock: scheduled effects → feeds → events (phase start) → physics step → each stage in order →
events (phase end) → metrics sampled → invariants and end conditions checked. A run ends when
an `end` condition holds, an effect `end`s it, or `clock.rounds` is used up.
On a continuous clock, elapsed physics advances before scheduled effects, feeds and start events at the new boundary.

A stage wakes agents (`who`, in `order`). An agent's turn is a short session: it reads its
brief + update, calls tools (legal actions, `look`, `inspect`, `end_turn`) until it ends the
turn, uses `max_actions`, or runs out of `max_calls`.
* `turns: sequential` — one agent at a time; actions apply immediately and the tool result is
  the actual outcome.
* `turns: simultaneous` — everyone sees the same state; actions are submitted, then committed in
  order after all have chosen (sealed bids, votes, simultaneous moves). Outcomes arrive as news.
* `until` repeats passes within the round (deliberation until everyone is ready).
* `quiet: skip` skips agents with nothing new since their last turn (from the second pass on;
  the first pass always wakes everyone).
* `look` and `inspect` are free reads: up to `max_calls` of them per turn use no call, and one past that is refused
  without spending a call, so an agent can always still act. The same read twice in a turn answers "Unchanged".
* A stage with `actions: []` wakes nobody: use it as a pure resolution step (`on_enter`/`on_exit`).
* `must_act: true` removes `end_turn` while an action is available; `on_idle` effects run for each agent
  that ends a turn without acting (`$actor`) — a forfeit or a default move. An agent that could act and did not — in
  a `must_act` stage, or any stage once its calls ran out — is reported as an `idle` event ("Ben did not act.").
* `terminal` may be an expression checked after the action applies (`"$world.jump_finished"`), so a
  move can end the turn only sometimes (multi-jumps).
* `time_limit` gives each agent wall-clock seconds for its turn (a number, or an expression over `$actor`;
  `env.run(..., time_limit=30)` covers stages that set none). Past it the turn ends, later calls are
  refused, a `timeout` event is logged and `on_timeout` runs instead of `on_idle`. The agent's update says
  how long it has. A participant that finishes in time plays exactly as it would without a limit.
* `atomic: true` makes a turn's actions apply together: each applies at once (the agent sees its result),
  but triggers, reactions and invariants wait until the turn ends. `valid` conditions (`$actor`, `$pending`)
  are checked when a turn that acted ends; if one fails, every action of the turn is undone, the agent is
  told `why` and plays the turn again (castling through check, a full backgammon move). `valid` makes a
  stage atomic. In a simultaneous stage each agent's choices commit or are undone together.
* Views with `"for": "spectator"` are an omniscient picture for UIs and reports: rendered at the end of
  every round into `result.frames` (the last marked `final`) and on demand by `env.spectate()`, never
  shown to an agent. They have no `$actor`; randomness they draw never changes the run.
* Physics precedes agent stages. Coupled world and entity equations share intermediate integration states.
  Continuous time starts at zero without a fictitious initial physics interval. Failed intervals restore
  equation state and property writebacks.
* Lifecycle hooks: `types.X.on_create` / `on_remove` run for every entity of X (and its subtypes; an
  ancestor's hooks first) the moment it is created or removed — by an effect, a mechanism or a hook —
  inside that change, so a `fail` in a hook refuses it. Entities made at build run on_create once the
  whole world exists, in creation order (`on_create_at_build: false` skips them). `$it` is the entity;
  in on_remove it is already no longer alive. Hooks setting off hooks stop at 16 levels.
* Invariants are checked after every action and effect block (and after physics): write them for states that
  must hold at all times, not ones that only settle at the end of a stage. An agent's action that breaks one —
  itself or through the triggers and hooks its commit sets off — is refused and undone, and the agent is told the
  invariant's `why` (give one: without it the agent only hears that a rule would break); the run goes on and its
  diagnostics count it. A break by anything else (events, physics, the build) fails the run. `"check": "round"`
  checks one only at the end of every round (a conservation sum over a big crowd then costs one pass a round, not
  one per change) — a break found then fails the run, whatever caused it; `"check": "end"` once, when the run
  finishes.
* An agent's action is one undoable unit: the checks of its call (requirements, arguments), its effects, and the
  hooks and triggers its commit sets off. A rule that fails anywhere in it (a division by zero, a number too large) refuses and undoes that action
  alone — in a sealed stage when the choices commit, in an atomic turn the whole turn — and the agent is told the cause
  without the rule or any hidden value. The run goes on; `result.diagnostics` names the failing rule with a fix and
  `stats.faulted_actions` counts these refusals. Guard such rules (`min`/`max` on the parameter, or a `when` with a
  `why`) so agents are told the limit up front. The same failure in events, world logic or physics fails the run, as
  do a host that fails and a crash in a mechanism's own code, wherever they happen.
* `end` conditions are checked after the start events, after each stage, and at the end of the round.
  `"check": "action"` also checks one the moment anything commits — an action, a sealed choice, an event or
  hook's effects — so a winning move ends the run before the next agent moves (in any kind of stage; sealed
  choices commit in turn order, so later ones are not applied). The `end` effect inside an action does the same.

What an agent reads:
* brief (static, cacheable): name, situation, rules, its identity and role text.
* update: time label and stage, why it is acting, "Since your last turn" (announcements of
  others' actions, outcomes of its own simultaneous actions, record entries, event news), then
  every declared view that applies. Text written by participants is wrapped «like this».
* tools: one per legal action with a JSON Schema (entity choices as enums, numeric bounds when
  they depend only on the actor), plus look/inspect/end_turn. Invalid calls return what to fix. An action's name is
  its tool's name, so it must be one providers accept (letters, digits, _ and -, at most 64) and not a built-in's.

Unless an action is `private` or sets `announce`, others read a default line
"Name: action (args)."; an action that posts to a record announces nothing extra (the entry
is the news). Text an agent types (text params) keeps its provenance wherever it is stored and
always renders «quoted», in news, views and outcomes.

An action applies atomically: if any effect `fail`s or a `transfer` lacks funds, every change
is rolled back and the agent is told why. Contract errors (bad expression at run time) stop
the run with status `failed` and the path of the broken rule.

