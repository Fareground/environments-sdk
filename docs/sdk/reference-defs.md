# defs

## `defs`: {name: expr | DefSpec}

Reusable expressions, called like built-ins: $utility($actor, 3).

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| expr | the def's args |

**DefSpec** — A named, reusable expression called like a built-in: ``$utility($actor, $params.offer)``.
Shorthand: the expression text (no arguments).
- `args`: [text] — Argument names; the body reads them as roots ($side).
- `expr`: text (required)
- `description`: text
