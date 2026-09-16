# outputs

## `outputs`: {output: expr | OutputSpec}

The typed results of a run.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| * | $outputs (earlier outputs) $result (winner, ended_by) |

**OutputSpec** — A typed field of the run result. ``$metrics.x`` is a metric's final value, ``$series.x`` its history.
- `expr`: text (required)
- `type`: text = "any" — One of: number, int, bool, text, list, map, any
- `description`: text
- `format`: text — How result.summary() and the CLI show it: a template format (money, pct, pct1, int, 0-4 decimals …); the stored value stays exact. Unset: numbers to 4 decimals.
