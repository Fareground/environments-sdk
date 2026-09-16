# checklist

## Quality checklist (what makes an environment great for LLM agents)

* Brief: situation and rules in a few plain sentences; the role text says what this agent wants.
* Views: only what matters for the decision, ranked (`sort`, `desc`) and capped (`limit`),
  names first with `[id]` handles, numbers with units (`|money`, `|pct`). Put rarely needed
  detail behind `look: true`.
* Tools: clear descriptions; tight params (`where`, `min`, `max`, `values`); `fail` messages that
  say what to do instead; `terminal: true` for the one decisive action of a turn.
* Stages: `simultaneous` for sealed choices; `max_actions` sized to the decision; `quiet: skip`
  in long deliberations.
* Measurement: metrics for the dynamics you care about; typed outputs for every result a caller
  needs; invariants for conservation laws.
* Always: run `check` until clean, `preview` every agent type, run a few seeds, read
  `result.stats` (invalid_rate and avg_update_tokens should stay low).

