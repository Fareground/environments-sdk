# inputs

## `inputs`: {name: InputSpec}

Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables.

**InputSpec** — A typed value supplied when the environment is loaded (``fg_env.load(..., inputs=)``).
- `type`: text = "number" — One of: number, int, bool, text, enum, list, table, map, date, any
- `default`: any — Used when the caller supplies nothing.
- `required`: bool = false — The caller must supply it (no default).
- `min`: number
- `max`: number
- `values`: [any] — Allowed values (type enum).
- `columns`: object — Column types (type table): {name: type}.
- `source`: text — Load the value from a data file (.csv → table, .json, .jsonl) inside the data directory: the contract file's folder, or `data_dir=` at load. Undeclared CSV columns stay text.
- `description`: text
- `unit`: text
