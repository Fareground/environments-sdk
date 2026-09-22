# records

## `records`: {record: RecordSpec}

Append-only logs (chat, reviews, bids) with per-viewer visibility; written with `post`.

Roots (plus everywhere: $inputs $world $physics $clock $round $stage $metrics $series $arm $pattern):
| where | extra roots |
|---|---|
| visible | $viewer $it (entry) |
| show | $it (entry: its fields directly, $it.text, plus author, round, seq, stage, to) |

**RecordSpec** — An append-only log (chat, reviews, bids, transcript). New entries reach agents as news.
- `fields`: object — {field: type}; text fields written by agents are marked untrusted.
- `show`: text — How one entry reads: '{author}: {text}'.
- `visible`: text = "all" — 'all' or an expression over $viewer and $it (the entry). It filters what agents are shown or offered; game logic reads every entry.
- `keep`: int — Keep only the latest N entries.
- `notify`: bool = true — Deliver new entries to agents in 'since your last turn'.
- `description`: text
