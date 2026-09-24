# functions / words

## Functions: words

- `$anagram(a, b)` — True when the two texts use exactly the same letters (case, spaces and punctuation ignored).
- `$mask(word, revealed, hidden?)` — Hangman view of `word`: letters in `revealed` (a list or text, case-insensitive) shown, other letters and digits replaced by `hidden` (default _); spaces and punctuation always shown.
- `$puzzle(kind, options?)` — A new puzzle with exactly one solution, drawn from the run's seed. sudoku: options {box: 2 or 3 (default), clues: the fewest clues to keep (default 0: remove all it can)} → {puzzle, solution, clues, box}; blanks are 0.
- `$solve(kind, problem, mode?)` — Solve or check a puzzle. mode count (default): {solutions: 0, 1 or 2 (two or more), unique, solution}. mode check (an attempt): sudoku → {valid, complete, solved, conflicts: [[row, col]]}; exact_cover (problem.chosen: set names) → {valid, complete, solved, overlaps, missing}. sudoku problem: rows with 0 or null for blanks; exact_cover problem: {sets: {name: [elements]}, universe?, chosen?}.
- `$wordle_feedback(guess, answer)` — Wordle marks per letter of `guess` against `answer` (same length, case-insensitive): green (right place), yellow (elsewhere, respecting repeated letters), gray.
