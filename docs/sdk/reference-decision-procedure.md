# decision / procedure

### `decision.procedure`
Rules of order. Phases: a state machine of named phases, each with its own stages, entry/exit effects, news and transitions by condition, rounds in phase, an event, or everyone having acted; terminal phases end the run; compiles to stages gated on $world.<name>_phase. Stack: items (motions, objections, spells) pushed by `<name>_<kind>` tools, answered in response windows by the agents each kind names, resolved last in, first out with each kind's effects (the `counter` action removes one unresolved); read it with $stack(name, read).

Config:
- `phases` (default {}): {phase: {title, brief, stages, on_enter, on_exit, say, next, terminal, winner}} in order. `next` is a phase name or [{to, when, after, event, all_did, say, do}] tried in order at the end of each round.
- `start` (default null): The first phase (default: the first listed).
- `views` (default true): Show every agent the current phase.
- `stack` (default null): A response stack: items pushed by `<name>_<kind>` tools or the `push` action, answered in the window stage `<name>_stack` (push an answer or `<name>_pass`) and resolved last in, first out.

Nested config:
**PhaseDef** — One phase.
- `title`: text — Name shown to agents.
- `brief`: text — Stage brief for this phase's stages that give none (template).
- `stages`: [object] — Stages (ordinary stage fields) that run during the phase.
- `on_enter`: [any] — Effects when the phase begins.
- `on_exit`: [any] — Effects when the phase ends.
- `say`: text — News when the phase begins (template).
- `next`: text | [Transition] — A phase name, or transitions tried in order.
- `terminal`: bool = false — The run ends at the end of the phase's first round (at once if it has no stages).
- `winner`: text — Terminal phases: expression naming the winner(s).
**Transition** — A way out of a phase. Every condition given must hold; none given = after one round.
- `to`: text (required) — The next phase.
- `when`: text — An expression that must hold.
- `after`: int | text — At least this many rounds in the phase.
- `event`: text — An event of this kind happened during the phase (an emit, a record, a vote).
- `all_did`: text — Every living entity of the action's `by` types took it during the phase, in any stage. Action conditions and stage filters do not narrow this group; other procedures using the same action can share completion evidence.
- `say`: text — News when it fires (template).
- `do`: effects — Effects when it fires.
**StackConfig** — A response stack: items agents answer, resolved last in, first out.
- `who`: text | [text] (required) — Agent type(s) that answer items (and push them unless a kind says `who`).
- `kinds`: object (required) — {kind: {title, who, params, starts, on, when, why, responders, show, on_push, resolve, countered, tool}}.
- `reopen`: bool = true — An item that comes back to the top (the one above resolved or was countered) gets a fresh response window.
- `silence`: any = "pass" — Ending a window turn without acting: pass, or keep owing an answer.
- `unanswered`: any = "pass" — Answers still owed when the window stage ends: pass (the stack resolves), or wait for the next round.
- `max_depth`: int = 16 — Most items on the stack at once.
- `passes`: int = 12 — Most passes of the window stage per round.
- `stage`: text — Hold windows in this declared stage instead of a generated `<name>_stack` stage.
- `views`: bool = true — Show the agents the stack while it holds items.
**StackKind** — One kind of item that can go on the stack (a motion, an objection, a spell).
- `title`: text — What it is called, in plain words (default: the kind's name).
- `description`: text — Tool description (default: generated from the rules).
- `who`: text | [text] — Agent type(s) that may push it (default: the stack's `who`).
- `params`: object — Tool params (ordinary param specs), kept on the item as $params.
- `starts`: bool = true — It may be pushed onto an empty stack.
- `on`: [text] — Kinds it may be pushed on top of (answer).
- `when`: text — Extra condition to push it ($actor, $top: the item it would answer, or null).
- `why`: text — What the agent is told when it may not push it.
- `responders`: text = "$it.id != $item.by" — Who owes it an answer while it is on top: an expression over $it (an agent) and $item.
- `show`: text — How the item reads after its title (template over $item, $params, $actor).
- `on_push`: [any] — Effects when it is pushed, e.g. paying a cost ($actor, $params, $item, $below).
- `resolve`: [any] — Effects when it resolves ($actor = who pushed it, $params, $item, $below).
- `countered`: [any] — Effects when it is countered instead of resolving.
- `tool`: bool = true — Generate the `<name>_<kind>` tool; false = pushed only by the push action in your actions.

Actions of the `decision` op:
- `push` — takes `item`, `params`, `who` (needs `item`): {"decision": "trial", "action": "push", "item": "exhibit", "params": {"name": "$params.name"}}  (push an item of a declared kind with its params; who pushes defaults to $actor)
- `pass` — takes `who`: {"decision": "trial", "action": "pass"}  (let the item on top of the stack stand; who defaults to $actor)
- `counter` — takes `target`: {"decision": "trial", "action": "counter"}  (remove an item without resolving it: target is an item id, default the item below the one resolving, else the top)

```json
{"mechanisms": {"my_procedure": {"kind": "decision", "mode": "procedure", "phases": {"debate": {"stages": [{"actions": ["speak"]}], "next": [{"to": "vote", "after": 2}]}, "vote": {"stages": [{"actions": ["vote"], "turns": "simultaneous"}], "terminal": true}}}}}
```
