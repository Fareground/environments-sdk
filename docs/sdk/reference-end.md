# end

## `end`: [EndSpec]

Conditions that end the run early, with an optional winner ($result.winner in outputs).

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| when/winner/say | — |

**EndSpec** — A condition that ends the run early.
- `when`: text (required)
- `name`: text
- `winner`: text — Expression naming the winner(s).
- `say`: text
- `check`: text = "stage" — When it is checked: stage (after the start events, after every stage and at the end of the round) | action (also the moment any action, sealed choice or effect block commits: a winning move ends the run at once).
