# functions / text

## Functions: text

- `$chars(text)` — The characters of text as a list.
- `$contains(text, part)` — True when `part` occurs in `text` (case-insensitive).
- `$ends_with(text, suffix)` — True when text ends with `suffix` (case-sensitive).
- `$fmt(value, format)` — The value as text in a template format: money, pct, int, 0–4 decimals, upper, …
- `$join(list, separator?)` — Items joined into text (default separator ', ').
- `$lower(text)` — Lower-case text.
- `$matches(text, pattern)` — True when the regular expression occurs in text. Linear-time subset: . [a-z] [^x] \d \w \s ^ $ ( ) (?: ) | * + ? {m,n}; no backreferences or lookaround.
- `$pad(text, width, fill?, side?)` — Text padded with `fill` (default a space) to `width` characters; `side` left (default), right or both.
- `$replace(text, old, new)` — Text with every `old` replaced by `new` (case-sensitive).
- `$similar(a, b)` — How alike two texts are, 0–1: 1 minus the edit (Levenshtein) distance over the longer length. Case-sensitive.
- `$split(text, separator?)` — Text cut into a list at each `separator` (default: runs of whitespace, ends trimmed).
- `$starts_with(text, prefix)` — True when text begins with `prefix` (case-sensitive).
- `$substr(text, start, end?)` — Characters from `start` up to (not including) `end`; negative positions count from the end.
- `$text(value)` — The value as text (entities give their name).
- `$title(text)` — Text with each word capitalised.
- `$trim(text)` — Text without leading and trailing whitespace.
- `$upper(text)` — Upper-case text.
- `$words(text)` — The words in text (letters and digits, keeping inner apostrophes and hyphens), punctuation dropped.
