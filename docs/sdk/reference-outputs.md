# outputs

## `outputs`: {output: expr | OutputSpec}

The typed results of a run; with `series: true` also sampled every round ($outputs.x latest, $series.x every round).

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| * | $outputs (series outputs' latest samples; earlier outputs, except in a sampled one) $result (winner, ended_by; not in a sampled one) |

**OutputSpec** — A typed field of the run result, worked out when the run ends. With ``series`` it is also sampled every round:
``$outputs.x`` reads its latest sample, ``$series.x`` every sample so far (``result.metrics`` and
``result.series``). One worked out from a private property (directly or through a def or another output) is not
shown to agents: `$outputs`/`$series` reads of it in what they are shown are refused.
- `expr`: text (required)
- `type`: text = "any" — One of: number, int, bool, text, list, map, any
- `description`: text
- `unit`: text
- `format`: text — How result.summary() and the CLI show it: a template format (money, pct, pct1, int, 0-4 decimals …); the stored value stays exact. Unset: numbers to 4 decimals.
- `series`: any | text = false — Also sample it every round: true samples `expr` (the result is the last sample); an expression samples that instead, when the per-round figure differs from the final one (sales each round, total sales at the end).
