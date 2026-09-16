# functions / text

## Functions: text

- `$anagram(a, b)` — True when the two texts use exactly the same letters (case, spaces and punctuation ignored).
- `$char_at(text, index)` — The character at `index` (negative counts from the end); an error when out of range.
- `$chars(text)` — The characters of text as a list.
- `$contains(text, part)` — True when `part` occurs in `text` (case-insensitive).
- `$count_text(text, part)` — How many times `part` occurs in text, not overlapping (case-sensitive).
- `$ends_with(text, suffix)` — True when text ends with `suffix` (case-sensitive).
- `$fmt(value, format)` — The value as text in a template format: money, pct, int, 0–4 decimals, upper, …
- `$index_of(text, part)` — Position of the first `part` in text (case-sensitive), or -1.
- `$join(list, separator?)` — Items joined into text (default separator ', ').
- `$lower(text)` — Lower-case text.
- `$mask(word, revealed, hidden?)` — Hangman view of `word`: letters in `revealed` (a list or text, case-insensitive) shown, other letters and digits replaced by `hidden` (default _); spaces and punctuation always shown.
- `$matches(text, pattern)` — True when the regular expression occurs in text. Linear-time subset: . [a-z] [^x] \d \w \s ^ $ ( ) (?: ) | * + ? {m,n}; no backreferences or lookaround.
- `$pad(text, width, fill?, side?)` — Text padded with `fill` (default a space) to `width` characters; `side` left (default), right or both.
- `$repeat_text(text, n)` — Text repeated `n` times.
- `$replace(text, old, new)` — Text with every `old` replaced by `new` (case-sensitive).
- `$similar(a, b)` — How alike two texts are, 0–1: 1 minus the edit (Levenshtein) distance over the longer length. Case-sensitive.
- `$split(text, separator?)` — Text cut into a list at each `separator` (default: runs of whitespace, ends trimmed).
- `$starts_with(text, prefix)` — True when text begins with `prefix` (case-sensitive).
- `$substr(text, start, end?)` — Characters from `start` up to (not including) `end`; negative positions count from the end.
- `$text(value)` — The value as text (entities give their name).
- `$title(text)` — Text with each word capitalised.
- `$trim(text)` — Text without leading and trailing whitespace.
- `$upper(text)` — Upper-case text.
- `$wordle_feedback(guess, answer)` — Wordle marks per letter of `guess` against `answer` (same length, case-insensitive): green (right place), yellow (elsewhere, respecting repeated letters), gray.
- `$words(text)` — The words in text (letters and digits, keeping inner apostrophes and hyphens), punctuation dropped.
