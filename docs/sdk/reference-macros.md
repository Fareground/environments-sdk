# macros

## Macros (repeat structure from data)

An object with `for` and `make` is a macro (data with only a `make` field, like a car's make, is not): it repeats `make` once per value, replacing `{name}`
placeholders (the `as` name) in strings and keys. Expanded when the contract is read, before
mechanisms, in every file on its own (see the result with `fg_env.expand(contract)` or `fg-env expand file.json`).

```json
"stages": [{"for": ["flop", "turn", "river"], "as": "street",
            "make": {"name": "{street}", "actions": ["bet_{street}"]}}],
"actions": {"bet_{street}": {"for": ["flop", "turn", "river"], "as": "street",
            "make": {"by": "player", "do": ["$actor.bets = $actor.bets + ['{street}']"]}}}
```

* In a list a macro becomes one item per value. As a map entry whose key holds one of its
  placeholders (`"bet_{street}"`) it becomes one entry per value; under any other key
  (`"stages": {"for": ...}`) it becomes the list of made values.
* `for`: a list (of values or objects), `{"range": n}` (0..n-1), `{"range": [start, end]}` or
  `{"range": [start, end, step]}` (the end excluded, like `$range`), or a placeholder giving a list
  from an outer loop (`"for": "{street.cards}"`).
* `{x}` alone in a string keeps the value's type (`"count": "{n}"` is a number); in longer text it is
  written out (lists and maps as JSON). `{x.field}` reads a field, `{x.0}` a list item, `{n+1}` and
  `{n-1}` offset a whole number. `"index": "i"` names the 0-based position.
* Nest loops by making a macro whose `make` is a macro: `"move_{a}_{b}"` with `for` a, `make` {`for` b …}.
* Only loop variables are replaced: template fields like `{name}` and `{{` stay as they are, so do
  not name a loop variable after a property you read in a template.
* Limits: 10,000 generated values per file, loops 8 deep. Two generated entries with one
  name, a missing field or a wrong `for` are errors with the macro's path.

