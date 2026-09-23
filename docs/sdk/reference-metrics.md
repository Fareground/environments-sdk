# metrics

## `metrics`: {metric: expr | MetricSpec}

Values sampled every round ($metrics.x latest, $series.x every round).

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| * | — |

**MetricSpec** — A number tracked every round (a series). Shorthand: the expression. One that names an agent's private property
(directly or through a def or metric) is not shown to agents: `$metrics`/`$series` reads of it in what they are
shown are refused.
- `expr`: text (required)
- `description`: text
- `unit`: text
