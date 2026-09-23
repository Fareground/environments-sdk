# stages

## `stages`: [StageSpec]

The steps of every round: who acts, how (sequential or sealed simultaneous), which actions.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| who/order/first_wake | $it $i |
| brief/time_limit/interval/on_wake/on_idle/on_turn_end/on_timeout | $actor |
| valid (expr and why) | $actor $pending |
| when/until/on_enter/on_exit | — |

**StageSpec** — One step of every round. Stages run in order; each wakes agents to take turns.
- `name`: text (required)
- `when`: text — Run this stage only when true (e.g. $round == 1).
- `actions`: text | [text] | object = "all" — 'all', a list, or {type: [actions]}.
- `turns`: text = "sequential" — sequential (one after another, effects immediate) | simultaneous (same picture, committed together) | scheduled (continuous clock: each agent whose wake time has come, earliest first).
- `interval`: number | text — Scheduled turns: time until an agent that took no timed action is woken again (number or expression over $actor; default clock.tick).
- `first_wake`: number | text — Scheduled turns: each agent's first wake time (number or expression over $it, $i; default 0).
- `order`: text — seat | random | expression over $it (lowest first): the order agents take turns in, and a simultaneous stage's choices commit in. Default: seat; a simultaneous stage's choices then commit in a random order drawn anew each time, so no seat always wins a contested item.
- `who`: text — Which agents are woken ($it); e.g. $it.alive && $chance(0.3).
- `until`: text — Repeat turns within the round until true.
- `passes`: int | text — Max passes through the agents (default 1, or 10 with until): a number ≥ 1 or an expression over $inputs.
- `quiet`: text = "wake" — wake | skip — skip agents with nothing new since their last turn.
- `max_actions`: int | text = 1 — Actions an agent may take per turn: a number or an expression over $inputs.
- `max_calls`: int | text = 8 — Tool calls (including looks) per turn: a number or an expression over $inputs.
- `brief`: text — Instruction shown during this stage (template).
- `must_act`: bool = false — While an action is available, the agent cannot just end its turn.
- `on_idle`: [any] — Effects for each agent that ends its turn without acting ($actor): a forfeit, a default move.
- `on_wake`: [any] — Effects for each agent just before its turn ($actor), so what it reads reflects them: an upkeep, a draw, marking news as seen.
- `on_turn_end`: [any] — Effects for each agent after its turn ($actor), whether or not it acted (simultaneous: after choices are committed).
- `auto`: bool = false — Play trivial turns without waking the agent: take the only legal action when it has no arguments, skip the turn when nothing is legal.
- `time_limit`: number | text — Wall-clock seconds each agent has for its turn (number, or expression over $actor; null uses the run's `time_limit`). Past it the turn ends, later calls are refused and `on_timeout` runs.
- `on_timeout`: [any] — Effects for each agent whose turn ran out of time ($actor), instead of `on_idle`.
- `atomic`: bool = false — The turn's actions apply together or not at all: triggers, reactions and invariants wait until the turn ends, and a turn that breaks `valid` is undone.
- `valid`: [Condition] — Conditions the whole turn must meet when it ends ($actor, $pending); if one fails, every action of the turn is undone and the agent is told `why` and plays the turn again. Makes the stage atomic.
- `on_enter`: effects
- `on_exit`: effects
