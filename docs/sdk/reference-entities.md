# entities

## `entities`: {id: EntitySpec}

Named entities (the name defaults to the id).

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| brief | $actor |

**EntitySpec** — A named starting entity.
- `type`: text (required)
- `name`: text
- `props`: object
- `at`: any
- `brief`: text — Private text added to this entity's own brief (template).
