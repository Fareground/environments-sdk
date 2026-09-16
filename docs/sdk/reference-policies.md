# policies

## `policies`: {policy: PolicySpec}

Coded participants as rules, for crowds and baselines (`policy:<name>`).

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| rules.* | $actor ($it $i with `each`) |

**PolicySpec** — A coded participant: the first rule whose condition holds and whose action is legal is taken.
- `rules`: [PolicyRule] (required)
- `repeat`: bool = false — Keep applying rules until the turn ends (default: one action).
**PolicyRule** — One rule of a coded policy: when `when` holds (and the `chance` roll passes), call `do` with `with`.
With `each`, the rule is tried once per item ($it): "for each of my armies, hold".
- `each`: text — A type or expression; the rule is tried for every item ($it).
- `when`: text
- `do`: effects (required) — Action name, or 'pass'.
- `with`: object — Params as values or expressions.
- `chance`: any | text
