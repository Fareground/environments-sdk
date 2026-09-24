# assets

## `assets`: {asset: AssetSpec}

Files beside the contract — images, PDFs, text, audio — delivered to agents under the visibility rules; see guide('assets').

**AssetSpec** — A file (or a folder of files) beside the contract. Its id is its name here; a folder's files are
`<name>/<file name>`. Referenced from `asset` properties, record fields and expressions (`$asset(id)`).
- `file`: text — Path of the file, relative to the contract's folder (or `data_dir=`), inside it.
- `folder`: text — Path of a folder inside the contract's folder: every file in it (not subfolders, not hidden files) becomes an asset `<name>/<file name>`.
- `type`: text — image | pdf | text | audio | file. Default: from the file extension; with `folder`, only files of this type are taken.
- `caption`: text — What the file shows, as agents read it next to the file ({name} is the file name).
- `alt`: text — A longer description for readers that cannot see the file (text-only models read it).
- `tags`: [text] — Labels for expressions: `'exhibit' in $asset(id).tags`.
- `max_bytes`: int — Largest file accepted (default: by type — image 10 MB, pdf 32 MB, text 2 MB, audio 25 MB, file 32 MB).
- `describe`: text — A host (a Describer) that writes a caption and extracted text for the file when the world is built, recorded on the host tape: `$asset(id).caption` and `.text` read it.
- `description`: text

## Files and media

An environment can carry real files — product photos, evidence, contracts, recordings — declared beside the
contract and delivered to agents under the same visibility rules as everything else.

```json
"assets": {
  "supply_agreement": {"file": "evidence/agreement.pdf", "caption": "The signed supply agreement", "tags": ["exhibit"]},
  "weld_photos": {"folder": "evidence/welds", "type": "image", "caption": "Weld photo {name}", "describe": "vision"}
},
"types": {"exhibit": {"props": {"file": {"type": "asset"}, "revealed": false}}},
"records": {"evidence": {"fields": {"text": "text", "file": "asset"}}},
"views": {"exhibits": {"of": "exhibit", "where": "$it.revealed", "show": "{name}", "attach": "$it.file"}},
"actions": {"file_photo": {"by": "attorney", "params": {"photo": {"type": "file", "kinds": ["image"]}},
                           "do": {"post": "evidence", "text": "New photo", "file": "$params.photo"}}}
```

**Declaring.** Each asset is a `file` or a `folder` inside the contract's folder (or `data_dir=`): an id is the
asset's name, a folder's files are `<name>/<file name>`. Types: image (png, jpg, webp, gif), pdf, text (txt, md),
audio (wav, mp3) and file (anything else; delivered by reference only). A file's content must match its
extension, and sizes are capped (image 10 MB, pdf 32 MB, text 2 MB, audio 25 MB, file 32 MB; `max_bytes`
changes it). Every file is hashed when the contract loads. A table input column of type `asset` names files by
path (`"columns": {"image": "asset"}`): the cell `photos/mug.jpg` is the asset `photos/mug.jpg`.

**Referencing.** A property or record field of type `asset` holds an asset id. `$asset(ref)` reads its details:
`id name type media_type size hash caption alt tags text submitted` — `text` is a text file's content or the
text a `describe` host extracted.

**Delivering.** An agent receives a file only through something it may see:
* a view's `attach` (an id, a list, or null per listed item — `where`, `for`, `stages` apply);
* a record entry's `asset` fields, wherever the entry reaches the agent (news or a view; `visible` and `to` apply);
* `brief.attach` (over `$actor`), its own action's `attach` (the tool result, or the outcome news of a sealed
  choice), and `inspect` (asset properties inspect shows: never another entity's private ones).
The text the agent reads carries a compact reference — `[image weld_1.png: "Crack along the seam"]` — and the
participant gets the files: `wake.attachments` (brief and update) and `result.attachments` (a tool result), each
with `type name media_type caption alt size hash`, `read()` for the bytes and `text()` for text files. Keep an
exhibit sealed with a private property or a `where` flag, and reveal it by posting a record entry or setting
the flag in a stage's `on_enter`; `fg-env check` warns when a view attaches another entity's private asset.

**Models.** `participants.anthropic(..., media=...)` and `participants.openai(..., media=...)` send real
content — Anthropic image and document blocks, OpenAI `image_url` data URLs, `file` and `input_audio` parts —
after a text part with each reference; types outside `media` (and `media=()`, for text-only models) are sent as
the reference alone: caption and alt text.

**Submitting.** A parameter of type `file` (`kinds`, `max_bytes`) takes a file from the agent:
`{"data": "<base64>", "name": "photo.jpg"}`, `{"text": "...", "name": "memo.md"}`, or `{"asset": id}` of a file
already submitted in the run; coded participants may pass bytes or call `wake.upload(bytes or path)` first. The
file is stored for the run (kind recognised from its bytes, never its name; size and kind checked) and
`$params.<name>` is its id (`upload:<hash>`), ready to put in an asset property or record field. Submitted files
are untrusted: their names and text reach others «quoted». Tool arguments never name paths.

**Hosts.** `{"host": "court", "action": "judge", ..., "attach": "$world.exhibits"}` and the game master's
`resolve` take `attach` too, and a judged record entry brings its own files: the request gets `attachments`
(metadata with base64 `data`, or `text`). An asset's `describe` host (`describe(request)` →
`{"caption", "text"}`) runs when the world is built. The reference host adapters send attachments as multimodal
content; `host.stubs.StubDescriber` answers offline. Every answer is on the host tape.

**Recordings and copies.** Snapshots, clones, forks and the engine's tape hold asset ids and content hashes,
never bytes; the exposure log lists, per wake, the assets shown (`assets: [{id, hash, in}]`) and a replay checks
them. Bytes are found by hash in this process; elsewhere, load the contract from its folder and call
`fg_env.assets.provide(folder)` for submitted files. `result.save("run.json")` writes every asset the run
knows into `run.assets/` beside it (files named by hash), and `RunResult.load` provides that folder again, so a
saved run with its contract folder replays anywhere.
