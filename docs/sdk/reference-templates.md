# templates

## Templates (show, outcome, announce, say, brief, id, name)

`"[{id}] {name} · {price|money} · {rating|1}★"`
* `{field}` reads the template's subject (`$it` in list views and record show, `$actor` in
  single-line views and stage briefs). Elsewhere use full expressions: `{$params.qty}`.
* `{$expr}` any expression, including ones that start with a quote: `{$'yes' if $x else 'no'}`.
  `{value|format}` formats: `money`, `pct`, `pct1`, `int`, `upper`, `lower`, `title`, `yesno`, `list`, `0`, `1`, `2`, `3`, `4`. In an expression use `$fmt(value, 'pct')`.
* A text field holding only an expression (`"say": "$inputs.headline"`) renders its value.
* Effect values (post fields, emit data, create props) that contain `{$…}` render as templates:
  `{"post": "log", "text": "{$actor.name} bid {$params.amount|money}"}`.
* Numbers print compactly; entities print as their name; lists join with commas; null is `—`.
* `{{` and `}}` are literal braces.

