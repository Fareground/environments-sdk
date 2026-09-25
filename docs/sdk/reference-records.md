# records

## `records`: {record: RecordSpec}

Append-only logs (chat, reviews, bids) with per-viewer visibility; written with `post`.

Roots (plus everywhere: $inputs $world $clock $round $stage $outputs $series $arm $pattern $physics $pending):
| where | extra roots |
|---|---|
| visible | $viewer $it (entry) |
| show | $it (entry: its fields directly, $it.text, plus author, round, stage, to, and seq: its number in what the reader sees of the record) |

**RecordSpec** — An append-only log (chat, reviews, bids, transcript). New entries reach agents as news.
- `fields`: object — {field: type}; text fields written by agents are marked untrusted.
- `show`: text — How one entry reads: '{author}: {text}'.
- `visible`: text = "all" — 'all' or an expression over $viewer and $it (the entry). It filters what agents are shown or offered; game logic reads every entry. An entry's `seq` counts from 1 in what each reader sees of the record (game logic: every entry of every record), so it never tells a reader of entries it cannot see.
- `keep`: int — Keep only the latest N entries.
- `notify`: bool = true — Deliver new entries to agents in 'since your last turn'.
- `description`: text
