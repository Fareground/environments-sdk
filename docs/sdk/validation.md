# Experiments and validation

## Separate three questions

1. **Is the contract valid?** `fg_env.check` finds structural and semantic issues, then plays the contract: 12 rounds (more to reach the last round a one-off event is scheduled for) with random agents, and again with each declared policy played by every agent type. Every check plays the same rounds; a time guard stops only a contract too slow to play, and reports it. A crash names the rule's path and the play that found it; a policy rule refused every time it was tried is a warning. So is a population that grows fast enough (agents creating agents) to pass the ceiling of 1,000,000 living entities before the run ends; a run that reaches it fails there. `--rounds N` plays exactly N rounds (0 = static only).
2. **Does it implement the brief?** Known-answer and boundary tests verify timing, balances, information and causal behavior.
3. **Does it predict the real system?** Calibration and held-out validation test against observations.

Passing one does not establish the others.

## Compare decisions

The inventory quickstart defines `baseline` and `busy` arms. Use the same policy and paired seeds:

```python
import fg_env

experiment = fg_env.experiment(
    "inventory.json",
    participants={"retailer": "policy:steady"},
    runs=20,
    arms=["baseline", "busy"],
)
print(experiment.table())
print(experiment.deltas("baseline"))
```

This introductory model has no random demand, so repeated runs are identical. Add justified uncertainty when the real process is uncertain. Paired seeds help compare arms; they do not remove modeling bias.

## Measure the business question

Define units and time windows for every output. Report fulfilled demand alongside unmet demand, cash alongside outstanding commitments, and campaign conversions alongside spend and capacity. Avoid a single score that hides tradeoffs.

## Fit and validate

The SDK exposes pattern fitting, calibration, sweeps, sensitivity, backtests and validation. Read [the running reference](reference-running.md) for call shapes and [optimization](reference-optimise.md) for decision search.

Keep fitting data separate from held-out evaluation. Compare to a simple baseline, inspect errors by product or segment, and report interval coverage where appropriate. Historical sales during stockouts may understate latent demand. Document such data limitations.

## A practical acceptance set

- One small case with hand-calculated results.
- Zero or exhausted resources, plus boundary inputs.
- Delays, cancellation and competing claims on capacity.
- More products, entities or branches than the original example.
- Visibility checks for every role.
- Snapshot continuation and recorded replay.
- A plausible intervention whose effect can be checked.

Keep the tests with the scenario, and rerun them when an authoring agent changes its rules.
