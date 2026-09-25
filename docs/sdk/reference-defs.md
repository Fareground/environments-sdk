# defs

## `defs`: {name: expr | DefSpec}

Reusable expressions, called like built-ins ($utility($actor, 3)), and effect lists (`do`), run with {"call": name, "with": {...}}.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| expr | the def's args |
| do | the def's args + locals |

**DefSpec** — A named, reusable piece of the rules. With ``expr`` it is an expression called like a built-in:
``$utility($actor, $params.offer)`` (shorthand: the expression text, no arguments). With ``do`` it is an effect
list run by the ``call`` effect: ``{"call": "settle", "with": {"buyer": "$actor"}}``; its effects see only the
arguments (plus $inputs, $world, $round …), never the caller's locals.
- `args`: [text] — Argument names; the body reads them as roots ($side).
- `expr`: text — The expression it gives.
- `do`: effects — The effects it runs, when called with {"call": name}.
- `description`: text
