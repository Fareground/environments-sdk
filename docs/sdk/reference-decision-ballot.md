# decision / ballot

### `decision.ballot`
A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted by plurality, majority or supermajority with an optional quorum when the vote's stage ends — after the contract's own events on its end, so read the result in a later stage or event, not in an event on the vote stage's end. The result is in $world.<name>_result ({winner, decided, passed, counts, ranking, votes, turnout, tie, vetoed}; an empty map until the first count) and is announced, options that are entity ids named — every time the vote's stage ends with a ballot some voter cast or abstained on; a ballot nobody touched leaves the last result standing: `decided` is true when there is a winner, `passed` when the first option won, so list a motion's yes first. Turnout counts the voters still in the game. `weight` gives shareholder-style votes, `threshold_of: members` measures the threshold over every member (cloture), `veto` lets some voters defeat a motion alone (a security council).

Config:
- `who` (required): Agent type that votes (subtypes included).
- `options` (required): The choices: a list, or an expression giving a list (e.g. "$map(candidate, $it.id)").
- `method` (default "plurality"): plurality (most votes) | majority (more than half) | supermajority (threshold, default 2/3) | approval (approve any number) | ranked (instant runoff) | borda | condorcet (Copeland); the last four take a list ballot. (score ballots are a map: count them with $tally_votes.)
- `threshold` (default null): Share of votes needed to pass (majority/supermajority).
- `threshold_of` (default "votes"): What the threshold is a share of: the votes cast (abstentions aside), or all members still in the game (e.g. cloture at 3/5 of the senate).
- `weight` (default null): Votes each voter casts, an expression over the voter $it (e.g. "$it.shares"); default 1. Turnout and quorum count weight too.
- `veto` (default null): Who holds a veto, an expression over the voter $it (e.g. "$it.permanent"): one of them voting for the second option defeats the first. Needs exactly two options, the motion first.
- `quorum` (default null): Share of eligible voters who must cast a ballot (abstentions count).
- `abstain` (default true): Voters may abstain.
- `private` (default true): Ballots stay private; only the result is announced.
- `ties` (default "random"): How a tie is decided (random uses the run's seed; first favours the first-declared option; none leaves it undecided, and in a ranked count eliminates every option tied for last together). A majority or supermajority tied at the top fails unless ties is first (a casting vote for the first option).
- `stage` (default null): Vote during this declared stage (tally at its end); default: a simultaneous stage named after the vote.
- `when` (default null): Hold the vote only when true (e.g. "$round == 3").
- `question` (default ""): What is being decided, shown with the ballot.
- `announce` (default ""): Result text (template over $result); default names the winner or says it failed.

Actions of the `decision` op:
- `tally`: {"decision": "election", "action": "tally"}  (count the ballot now: sets $world.election_result, announces it, opens a fresh ballot)

```json
{"mechanisms": {"my_ballot": {"kind": "decision", "mode": "ballot", "who": "member", "options": ["approve", "reject"], "method": "majority", "quorum": 0.5, "question": "Adopt the budget?"}}}
```
