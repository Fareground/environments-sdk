# invariants

## `invariants`: [expr | InvariantSpec]

Rules that must always hold; a violation fails the run.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| * | — |

**InvariantSpec** — Must always hold. A violation fails the run.
- `expr`: text (required)
- `why`: text
- `check`: text = "action" — When it is checked: action (after the build, every action and effect block, and every round) | round (after the build and at the end of every round: much cheaper for sums over big crowds) | end (once, when the run finishes).
