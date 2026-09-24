# decision

## Mechanism family `decision`

Collective choice: ballots, structured deliberation with motions and votes, and procedures with phases and objections.

Named the same in every mode:
- `who`: agent type that decides
- `ties`: how a tie is decided: random (from the run's seed) | first (declared order) | none (nobody wins) | share
- `private`: keep choices hidden from other agents
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `when`: only while this expression is true
- `views`: generate the mechanism's views

Modes (`"kind": "decision", "mode": ...`; read one with `guide('decision.<mode>')`):
- `ballot`: A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted by plurality, majority or supermajority with an optional quorum when the vote's stage ends — after that stage's own on_exit effects, so read the result in a later stage, event or on_enter, not in the vote stage's on_exit.
- `deliberation`: A deliberating body: a discussion stage `<name>` that repeats passes until every member is ready (or the pass cap), optional chair with floor control (raise hand, recognize, speaker limits), motions with seconds, amendments, calling the question, and a vote stage `<name>_vote` counted by majority or supermajority.
- `procedure`: Rules of order.

Functions:
- `$decisions(mechanism?)` — Decided main motions, oldest first: [{id, text, passed, counts, round}].
- `$discussion_over(mechanism?)` — True when the discussion should stop this round: a vote is due, or every member (the last speaker aside) is ready.
- `$house(viewer, mechanism?)` — The state of the deliberation as the viewer should read it: question, floor, hands, readiness.
- `$pending_motion(mechanism?)` — The question before the body (the top motion or amendment) as {id, kind, text, mover, seconder, status, speeches, target}, or null (deliberation mechanism).
- `$stack(procedure, read?, ...)` — A procedure's response stack. read: items (default; bottom first: [{id, kind, title, by, params, on, round, waiting}], `on` the id of the item it answers, `waiting` who still owes it an answer) | top (the top item or null) | waiting (ids who owe the top an answer; with an agent, whether it does) | can_push (kind, agent: whether it may push that kind now) | text (viewer?: the stack as lines, with what the viewer may answer).
