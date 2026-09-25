# host / judge

### `host.judge`
A rubric judge answered by a host evaluator: the `judge` action (`text` and `subject`) in any effect list (or every new entry of `record`) scores the text per criterion, alone or as a panel, optionally blind. Each verdict is posted to the record <name> (subject, scores, total, rationale «quoted», stand_in: true when the midpoint fallback scored it, not an evaluator) and added to $world.<name>_totals and the `into` property, recorded for replay.

Config:
- `criteria` (required): {criterion: {description, weight, scale}}.
- `instructions` (default ""): What the judge is judging and how (plain text).
- `host` (default "judge"): Host evaluator name.
- `model` (default null): A name for the kind of model wanted (e.g. "strong"), which the host maps to one of its own models (LLMHost(..., models={...})); a host that does not map it uses its own model.
- `panel` (default []): Several judges; scores are aggregated per criterion.
- `aggregate` (default "mean"): How a panel's scores combine (trimmed_mean drops the highest and lowest with 3+ judges).
- `out_of` (default 10): The total is the weighted rubric score on a 0..out_of scale.
- `who` (default null): Type of the entities judged (for `into` and blind aliases).
- `into` (default null): Number property on `who` that accumulates each total.
- `record` (default null): Judge every new entry of this record automatically.
- `field` (default "text"): The judged field of `record` entries.
- `stage` (default null): Judge new `record` entries at the end of this stage (default: at the end of every round).
- `context_last` (default 0): Earlier `record` entries shown to the judge as context.
- `blind` (default false): The judge sees 'Participant A/B/…' instead of names.
- `visible` (default "all"): Who reads the scores: 'all' or an expression over $viewer and $it.
- `notify` (default true): Deliver scores to agents as news.
- `fallback` (default null): Without an evaluator: score every criterion at its midpoint, so every entry ties; the run's diagnostics report it (default: stop with an error).

Nested config:
**Criterion** — One rubric criterion.
- `description`: text — What this criterion rewards.
- `weight`: number = 1.0 — Relative weight in the total.
- `scale`: any = [1, 10] — [lowest, highest] score.
**Seat** — One judge of a panel.
- `name`: text (required) — The judge's name (it is asked as this judge).
- `host`: text — Host answering for this judge (default: the mechanism's host).
- `model`: text — A name for the kind of model wanted (e.g. "strong"), which the host maps to one of its own models (LLMHost(..., models={...})); a host that does not map it uses its own model.

Actions of the `host` op:
- `judge` — takes `text`, `subject`, `entry`, `context`, `attach`: {"host": "speeches", "action": "judge", "text": "$params.text", "subject": "$actor"}  (score `text`, or a record `entry`, with the judge; the verdict goes to the record speeches and its totals; without either, judge the new entries of its `record`; `attach` gives the judge files, and a judged entry brings its own)

```json
{"types": {"debater": {"agent": true, "props": {"score": 0}}}, "records": {"speeches": {"fields": {"text": "text"}}}, "actions": {"speak": {"by": "debater", "params": {"text": {"type": "text"}}, "do": [{"post": "speeches", "text": "$params.text"}]}}, "entities": {"debater": {"type": "debater", "count": 2}}, "mechanisms": {"my_judge": {"kind": "host", "mode": "judge", "record": "speeches", "who": "debater", "into": "score", "criteria": {"logic": {"weight": 2}, "evidence": {"scale": [1, 5]}}, "instructions": "Judge each debate speech on its merits."}}}
```
