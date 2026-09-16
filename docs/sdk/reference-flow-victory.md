# flow / victory

### `flow.victory`
Win conditions: first to a score, highest score at a round, last one standing, last team, cooperative win or loss, a condition held for K rounds, eliminating a type, or completing objectives — with tiebreaks and tie rules. Generates `end` entries (and end-of-round events for `most` and `stable`); $best(items, by, ties) resolves winners anywhere. For an agent type it fills the `game` section: seats and returns ($won).

Config:
- `who` (required): The type whose members can win (subtypes included).
- `alive` (default "true"): Who is still in ($it), e.g. $it.hp > 0 — any condition, not only the built-in `alive` (false once an entity is removed).
- `conditions` (required): Tried in order; each one of {first_to + score}, {most, at}, {last_standing: true}, {last_team}, {win_when}, {lose_when}, {stable, rounds}, {eliminate, where}, {objectives}; plus name, winner, say.
- `tiebreak` (default []): Expressions ($it) that decide ties in order, highest first.
- `ties` (default "share"): A tie left after tiebreaks: share the win, none (nobody wins) or pick at random (seeded).

Nested config:
**VictoryCondition** — One way the game ends. Give exactly one of the kinds.
- `first_to`: number | text — A player whose `score` reaches this wins.
- `score`: text — The score for first_to ($it).
- `most`: text — Highest of this score ($it) wins at round `at`.
- `at`: int | text — Round most is decided (default: the last round).
- `last_standing`: bool — The last player still in wins.
- `last_team`: text — Team of each player ($it): the last team with players in wins.
- `win_when`: text — Cooperative win: every player still in wins when true.
- `lose_when`: text — Cooperative loss: nobody wins when true.
- `stable`: text — Ends when this holds at the end of `rounds` rounds in a row.
- `rounds`: int — Rounds in a row for stable.
- `eliminate`: text — A type: when none of it is left (see where), the players still in win.
- `where`: text — Which of the eliminate type count ($it).
- `objectives`: [text] — A player for whom all these hold ($it) wins.
- `name`: text — How the run's ended_by reads (default: the kind).
- `winner`: text — Expression naming the winner instead of the default.
- `say`: text — Announcement (template); default names the winner.

```json
{"mechanisms": {"my_victory": {"kind": "flow", "mode": "victory", "who": "player", "alive": "not $it.bankrupt", "conditions": [{"first_to": 10, "score": "$it.points"}, {"last_standing": true}, {"most": "$it.points"}], "tiebreak": ["$it.cash"]}}}
```
