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
* Measurement: series outputs for the dynamics you care about; typed outputs for every result a caller
  needs; invariants for conservation laws.
* Always: run `check` until clean, `preview` every agent type, run a few seeds, read
  `result.stats` (invalid_rate and avg_update_tokens should stay low; faulted_actions should be 0) and
  `result.degraded` (empty for a run that shows how the environment plays).

Three questions, each answered separately: is the contract valid (`check`)? Does it implement the brief (known-answer
and boundary tests)? Does it predict the real system (calibration and held-out validation, `fg_env.analysis`)?
Passing one says nothing about the others. A practical acceptance set, kept beside the contract and run again after
every change: one small case worked out by hand; zero and exhausted resources and boundary inputs; delays and
competing claims on capacity; more entities than the brief's example; what every role sees; a snapshot continued and
a recorded run replayed; and one intervention whose effect you can predict.

