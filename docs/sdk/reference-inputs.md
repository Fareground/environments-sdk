# inputs

## `inputs`: {name: InputSpec}

Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables.

**InputSpec** — A typed value supplied when the environment is loaded (``fg_env.load(..., inputs=)``).
- `type`: text = "number" — One of: number, int, bool, text, enum, list, table, map, date, any
- `default`: any — Used when the caller supplies nothing.
- `required`: bool = false — The caller must supply it (no default).
- `min`: number
- `max`: number
- `multiple_of`: number — Require a multiple of this positive numeric increment, measured from zero; e.g. 0.01 for cents. Unlike step, validates supplied data.
- `values`: [any] — Allowed values (type enum).
- `columns`: object — Column types (type table): {name: type}.
- `source`: text — Load the value from a data file (.csv → table, .json, .jsonl) inside the data directory: the contract file's folder, or `data_dir=` at load. Undeclared CSV columns stay text.
- `description`: text
- `unit`: text
- `label`: text — Human-readable input label; defaults to the input name in a host UI.
- `display`: any — Optional host UI control. Presentation only: does not change simulation semantics.
- `step`: number — Suggested numeric control increment; min/max still validate supplied values.
- `fields`: object — Typed configurable fields of a map object or each table row; supports nested objects, defaults and control hints.
- `items`: InputSpec — Typed elements of a list input.
