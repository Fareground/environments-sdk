# functions / decision

## Functions: decision

- `$decisions()` — Decided main motions, oldest first: [{id, text, passed, counts, round}].
- `$discussion_over()` — True when the discussion should stop this round: a vote is due, or every member (the last speaker aside) is ready.
- `$house(viewer)` — The state of the deliberation as the viewer should read it: question, floor, hands, readiness.
- `$pending_motion()` — The question before the body (the top motion or amendment) as {id, kind, text, mover, seconder, status, speeches, target}, or null (deliberation mechanism).
- `$tally_votes(method, ballots, options?, threshold?, ties?)` — Count ballots with a voting method (plurality, majority, supermajority, approval, ranked, borda, score, condorcet). ballots: {voter: ballot} or a list; returns {winner, decided, passed, counts, ranking, votes, tie, tied, share, rounds}: decided = there is a winner, passed = the first option won (list a motion's yes first). ties: random (seeded) | none | first.
