# decision / ballot

### `decision.ballot`
A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted by plurality, majority or supermajority with an optional quorum when the vote's stage ends — after that stage's own on_exit effects, so read the result in a later stage, event or on_enter, not in the vote stage's on_exit. The result is in $world.<name>_result ({winner, passed, counts, ranking, votes, turnout, tie}; an empty map until the first count) and is announced, options that are entity ids named. Turnout counts the voters still in the game.

Config:
- `who` (required): Agent type that votes (subtypes included).
- `options` (required): The choices: a list, or an expression giving a list (e.g. "$map(candidate, $it.id)").
- `method` (default "plurality"): plurality (most votes) | majority (more than half) | supermajority (threshold, default 2/3) | approval (approve any number) | ranked (instant runoff) | borda | condorcet (Copeland); the last four take a list ballot.
- `threshold` (default null): Share of votes needed to pass (majority/supermajority).
- `quorum` (default null): Share of eligible voters who must cast a ballot (abstentions count).
- `abstain` (default true): Voters may abstain.
- `private` (default true): Ballots stay private; only the result is announced.
- `ties` (default "random"): How a tie is decided (random uses the run's seed).
- `stage` (default null): Vote during this declared stage (tally at its end); default: a simultaneous stage named after the vote.
- `when` (default null): Hold the vote only when true (e.g. "$round == 3").
- `question` (default ""): What is being decided, shown with the ballot.
- `announce` (default ""): Result text (template over $result); default names the winner or says it failed.
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Actions of the `decision` op:
- `tally`: {"decision": "election", "action": "tally"}  (count the ballot now: sets $world.election_result, announces it, opens a fresh ballot)

```json
{"mechanisms": {"my_ballot": {"kind": "decision", "mode": "ballot", "who": "member", "options": ["approve", "reject"], "method": "majority", "quorum": 0.5, "question": "Adopt the budget?"}}}
```
