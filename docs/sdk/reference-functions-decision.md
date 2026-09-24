# functions / decision

## Functions: decision

- `$decisions(mechanism?)` — Decided main motions, oldest first: [{id, text, passed, counts, round}].
- `$discussion_over(mechanism?)` — True when the discussion should stop this round: a vote is due, or every member (the last speaker aside) is ready.
- `$house(viewer, mechanism?)` — The state of the deliberation as the viewer should read it: question, floor, hands, readiness.
- `$pending_motion(mechanism?)` — The question before the body (the top motion or amendment) as {id, kind, text, mover, seconder, status, speeches, target}, or null (deliberation mechanism).
- `$stack(procedure, read?, ...)` — A procedure's response stack. read: items (default; bottom first: [{id, kind, title, by, params, on, round, waiting}], `on` the id of the item it answers, `waiting` who still owes it an answer) | top (the top item or null) | waiting (ids who owe the top an answer; with an agent, whether it does) | can_push (kind, agent: whether it may push that kind now) | text (viewer?: the stack as lines, with what the viewer may answer).
