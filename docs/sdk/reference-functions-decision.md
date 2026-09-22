# functions / decision

## Functions: decision

- `$decisions(mechanism?)` — Decided main motions, oldest first: [{id, text, passed, counts, round}].
- `$discussion_over(mechanism?)` — True when the discussion should stop this round: a vote is due, or every member (the last speaker aside) is ready.
- `$house(viewer, mechanism?)` — The state of the deliberation as the viewer should read it: question, floor, hands, readiness.
- `$pending_motion(mechanism?)` — The question before the body (the top motion or amendment) as {id, kind, text, mover, seconder, status, speeches, target}, or null (deliberation mechanism).
- `$tally_votes(method, ballots, options?, threshold?, ties?)` — Count ballots with a voting method (plurality, majority, supermajority, approval, ranked, borda, score, condorcet). ballots: {voter: ballot} or a list; returns {winner, passed, counts, ranking, votes, tie, tied, share, rounds}. ties: random (seeded) | none | first.
