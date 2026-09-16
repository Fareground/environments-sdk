# blocks

## `blocks`: {name: BlockSpec}

Reusable effect lists, run with {"block": name, "with": {...}}.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| do | the block's args + locals |

**BlockSpec** — A named, reusable effect list: ``{"block": "settle", "with": {"buyer": "$actor"}}``.
The effects see only the arguments (plus $inputs, $world, $round …), never the caller's locals.
- `args`: [text]
- `do`: effects (required)
- `description`: text
