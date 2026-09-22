# Town hall vote

Our town is holding a town hall meeting about three proposals: a new park, a parking garage and longer library hours. Nine residents attend, each an AI agent with their own priorities. First there is a discussion where each resident can speak once per round for two rounds. Then every resident votes yes, no or abstain on each proposal. Ballots are secret: nobody may learn how any other resident voted, only the totals. A proposal passes if it gets more yes votes than no votes, so a tie fails. A vote only counts if at least five residents voted yes or no on that proposal; otherwise the result is "no quorum".

## Deliverables

Report these outputs, using `park`, `garage` and `library` as the proposal keys:
- `results` — a map from proposal to "passed", "failed" or "no quorum".
- `tallies` — a map from proposal to a map with the counts `yes`, `no` and `abstain`.
- `ballots` — a map from each resident's id to a map from proposal to "yes", "no", "abstain", or null if they did not vote on it.
