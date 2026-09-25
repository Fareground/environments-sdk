# stages

## `stages`: [StageSpec]

The steps of every round: who acts, how (sequential or sealed simultaneous), which actions.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| who/order | $it $i |
| brief | $actor |
| valid (expr and why) | $actor $pending |
| when/until | — |

**StageSpec** — One step of every round. Stages run in order; each wakes agents to take turns. What happens around a stage (a
resolution when it ends, a default move for an agent that did not act) is an event on the stage's anchors.
- `name`: text (required)
- `when`: text — Run this stage only when true (e.g. $round == 1, $round % 7 == 0).
- `actions`: text | [text] | object = "all" — 'all', a list, or {type: [actions]}.
- `turns`: text = "sequential" — sequential (one after another, effects immediate) | simultaneous (everyone chooses from the same picture; the sealed choices then commit one agent after another, in `order` or else a random order — resolve them jointly in an event on `stage.<name>.end`).
- `order`: text — seat | random | expression over $it (lowest first): the order agents take turns in, and a simultaneous stage's choices commit in. Every agent sees it, so it may read no agent's private property. Default: seat; a simultaneous stage's choices then commit in a random order drawn anew each time, so no seat always wins a contested item.
- `who`: text — Which agents are woken ($it); e.g. $it.alive and $chance(0.3).
- `until`: text — Repeat turns within the round until true.
- `passes`: int | text — Max passes through the agents (default 1, or 10 with until): a number ≥ 1 or an expression over $inputs.
- `quiet`: text = "wake" — wake | skip — skip agents with nothing new since their last turn.
- `max_actions`: int | text = 1 — Actions an agent may take per turn: a number or an expression over $inputs.
- `max_calls`: int | text = 8 — Tool calls (including looks) per turn: a number or an expression over $inputs.
- `brief`: text — Instruction shown during this stage (template).
- `must_act`: bool = false — While an action is available, the agent cannot just end its turn.
- `valid`: [Condition] — Conditions the whole turn must meet when it ends, after the `change` events it sets off ($actor, $pending); if one fails, every action of the turn is undone and the agent is told `why` and plays the turn again. The turn's actions apply together or not at all: events, reactions and invariants wait until it ends, and an action's `outcome` (and attached files) is shown once the turn commits. `"true"` makes the turn atomic with no condition. An action that draws randomness settles the turn so far at once (a failure then undoes the turn and ends it), so no later action can undo its luck.
