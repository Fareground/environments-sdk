# inputs

## `inputs`: {name: InputSpec}

Typed values supplied when the contract is loaded ($inputs.x): knobs, data tables, and the files the environment carries (`type: file`; see guide('assets')).

**InputSpec** — A typed value supplied when the environment is loaded (``fg_env.load(..., inputs=)``).
- `type`: text = "number" — One of: number, int, bool, text, enum, list, table, map, date, file, any (`integer`, `float`, `string` and `boolean` are read as int, number, text and bool)
- `default`: any — Used when the caller supplies nothing.
- `required`: bool = false — The caller must supply it (no default).
- `min`: number — The least value of a number; the fewest rows of a table or items of a list.
- `max`: number — The greatest value of a number; the most rows of a table or items of a list.
- `multiple_of`: number — Require a multiple of this positive numeric increment, measured from zero; e.g. 0.01 for cents. Unlike step, validates supplied data.
- `values`: [any] — Allowed values (type enum).
- `columns`: object — Column types (type table): {name: type}.
- `source`: text — Load the value from a data file (.csv → table, .json, .jsonl) inside the data directory: the contract file's folder, or `data_dir=` at load. Undeclared CSV columns stay text. A `file` input carries the file (an image, PDF, text, audio) or every file of the folder named here.
- `description`: text
- `unit`: text
- `label`: text — Human-readable input label; defaults to the input name in a host UI.
- `display`: any — Optional host UI control. Presentation only: does not change simulation semantics.
- `step`: number — Suggested numeric control increment; min/max still validate supplied values.
- `fields`: object — Typed configurable fields of a map object or each table row; supports nested objects, defaults and control hints.
- `items`: InputSpec — Typed elements of a list input.
- `caption`: text — file: what the file shows, as agents read it next to the file ({name} is the file name).
- `alt`: text — file: a longer description for readers that cannot see the file (text-only models read it).
- `tags`: [text] — file: labels for expressions: `'exhibit' in $asset(id).tags`.
- `max_bytes`: int — file: the largest file accepted (default: by kind — image 10 MB, pdf 32 MB, text 2 MB, audio 25 MB, other 32 MB).
- `describe`: text — file: a host (a Describer) that writes a caption and extracted text for the file when the world is built, recorded on the host tape: `$asset(id).caption` and `.text` read it.
