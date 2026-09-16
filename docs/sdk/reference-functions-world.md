# functions / world

## Functions: world

- `$asset(ref)` — An asset's details, or null for null: `id name type media_type size hash caption alt tags text submitted` (`text` is a text file's content or the text a describe host extracted; `caption` falls back to the described one). `ref` is an asset id or an `asset` property.
- `$entity(id)` — The entity with this id, or null.
- `$events(kind?, where?)` — Events so far (optionally of one kind), oldest first. Reads follow $viewer, else $actor; with neither, all events. Record events obey their retained entry's visibility.
- `$exists(id)` — True when an alive entity with this id exists.
- `$pattern_values(name)` — Every key's value of a keyed pattern now, as {key: value}: $pattern_values('season').
- `$records(name, where?)` — Entries of a declared record, oldest first.
- `$seen(agent, item)` — Whether `agent` was shown `item` on a wake so far — an event (from $events), a record entry (from $records) or a view by name. Needs the exposure log, which a contract that calls $seen keeps.
