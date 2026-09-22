# decision

## Mechanism family `decision`

Collective choice: ballots and structured deliberation with motions and votes.

Named the same in every mode:
- `who`: agent type that decides
- `ties`: how a tie is decided: random (from the run's seed) | first (declared order) | none (nobody wins) | share
- `private`: keep choices hidden from other agents
- `stage`: a declared stage the mechanism runs in (default: a stage it generates)
- `when`: only while this expression is true
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "decision", "mode": ...`; read one with `guide('decision.<mode>')`):
- `ballot`: A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted by plurality, majority or supermajority with an optional quorum when the vote's stage ends — after that stage's own on_exit effects, so read the result in a later stage, event or on_enter, not in the vote stage's on_exit.
- `deliberation`: A deliberating body: a discussion stage `<name>` that repeats passes until every member is ready (or the pass cap), optional chair with floor control (raise hand, recognize, speaker limits), motions with seconds, amendments, calling the question, and a vote stage `<name>_vote` counted by majority or supermajority.

Functions:
- `$decisions(mechanism?)` — Decided main motions, oldest first: [{id, text, passed, counts, round}].
- `$discussion_over(mechanism?)` — True when the discussion should stop this round: a vote is due, or every member (the last speaker aside) is ready.
- `$house(viewer, mechanism?)` — The state of the deliberation as the viewer should read it: question, floor, hands, readiness.
- `$pending_motion(mechanism?)` — The question before the body (the top motion or amendment) as {id, kind, text, mover, seconder, status, speeches, target}, or null (deliberation mechanism).
- `$tally_votes(method, ballots, options?, threshold?, ties?)` — Count ballots with a voting method (plurality, majority, supermajority, approval, ranked, borda, score, condorcet). ballots: {voter: ballot} or a list; returns {winner, passed, counts, ranking, votes, tie, tied, share, rounds}. ties: random (seeded) | none | first.
