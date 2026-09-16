# decision / deliberation

### `decision.deliberation`
A deliberating body: a discussion stage `<name>` that repeats passes until every member is ready (or the pass cap), optional chair with floor control (raise hand, recognize, speaker limits), motions with seconds, amendments, calling the question, and a vote stage `<name>_vote` counted by majority or supermajority. Read state with $pending_motion(), $decisions(), $discussion_over(), $house(viewer).

Config:
- `who` (required): Agent type that deliberates and votes.
- `chair` (default null): Agent type of the chair: recognizes speakers and calls the question.
- `question` (default ""): What the body is deliberating, shown every turn.
- `floor` (default false): Floor control (needs a chair): members raise hands and speak only when recognized.
- `speaker_limit` (default 2): Speeches per recognition before the floor returns to the chair.
- `passes` (default 6): Most passes of discussion per round (the backstop).
- `max_chars` (default 600): Longest speech or motion, in characters.
- `motions` (default true): Members may propose motions.
- `second` (default true): A motion or amendment needs a second before debate.
- `amendments` (default true): Members may amend the open motion.
- `min_debate` (default 1): Speeches on a question before a member may call it (no chair).
- `method` (default "majority"): majority (more than half of votes cast) | supermajority (threshold, default 2/3).
- `threshold` (default null): Share of votes needed to pass.
- `quorum` (default null): Share of members who must vote (abstentions count).
- `private` (default false): Votes stay secret and only the result is announced; otherwise each vote is announced.
- `ready_when_silent` (default true): Ending a discussion turn without acting marks the member ready.
- `vote_when_ready` (default true): When every member is ready, an open question goes to a vote.
- `backstop` (default "adjourn"): When the pass cap is hit: adjourn to the next round, or vote on the open question.
- `end` (default "decision"): End the run once a main motion is decided, only once one passes, or never.
- `when` (default null): Hold the discussion only when true (e.g. "$round <= 5").
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Actions of the `decision` op:
- `speak` — takes `text` (needs `text`): {"decision": "hall", "action": "speak", "text": "$params.text"}  (a speech; under floor control only by the member holding the floor)
- `ready`: {"decision": "hall", "action": "ready"}  (nothing more to add this round)
- `raise_hand`: {"decision": "hall", "action": "raise_hand"}  (ask the chair for the floor)
- `recognize` — takes `who` (needs `who`): {"decision": "hall", "action": "recognize", "who": "$params.who"}  (the chair gives the floor to a member whose hand is raised)
- `yield`: {"decision": "hall", "action": "yield"}  (give the floor back to the chair)
- `propose` — takes `text` (needs `text`): {"decision": "hall", "action": "propose", "text": "$params.text"}  (move a motion)
- `second`: {"decision": "hall", "action": "second"}  (second the proposal waiting for a second)
- `amend` — takes `text` (needs `text`): {"decision": "hall", "action": "amend", "text": "$params.text"}  (new wording for the open motion, voted on before it)
- `withdraw`: {"decision": "hall", "action": "withdraw"}  (withdraw your pending motion or amendment)
- `call_question`: {"decision": "hall", "action": "call_question"}  (end debate and put the open question to a vote)
- `vote` — takes `choice` (needs `choice`): {"decision": "hall", "action": "vote", "choice": "$params.choice"}  (yes, no or abstain on the question put)

```json
{"mechanisms": {"my_deliberation": {"kind": "decision", "mode": "deliberation", "who": "resident", "chair": "moderator", "floor": true, "question": "Should the town build a skate park?", "passes": 8}}}
```
