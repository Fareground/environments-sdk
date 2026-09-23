# invariants

## `invariants`: [expr | InvariantSpec]

Rules that must always hold. An agent's action that breaks one is refused and undone (the `why` is its reason); a break by anything else fails the run.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| * | — |

**InvariantSpec** — Must always hold. Broken by an agent's action (with everything its commit sets off), that action is refused and
undone and the agent told `why`; broken by anything else (events, physics, the build), the run fails.
- `expr`: text (required)
- `why`: text
- `check`: text = "action" — When it is checked: action (after the build, every action and effect block — an `each` event once its last item ran, or before a trigger or reaction an item sets off — and every round; `$all(<type>, <condition>)` over each member's own properties re-checks only the members that changed) | round (after the build and at the end of every round: much cheaper for sums over big crowds) | end (once, when the run finishes).
