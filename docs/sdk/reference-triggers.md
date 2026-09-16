# triggers

## `triggers`: [TriggerSpec]

What the world does the moment a condition becomes true (checked after every action and effect block), unlike an event, which runs at a set point of the round.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| when/do/say | — |

**TriggerSpec** — World logic that reacts the moment a condition becomes true — after any action, effect,
physics step or round end — instead of waiting for the next event phase.
- `name`: text
- `when`: text (required) — Fires when this becomes true (it re-arms once it is false again).
- `do`: effects
- `say`: text — Headline agents receive as news.
- `once`: bool = false — Fire at most once per run.
- `arms`: [text] — Only in these experiment arms.
