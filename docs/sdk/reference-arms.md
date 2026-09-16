# arms

## `arms`: {arm: ArmSpec}

Experiment variants: input overrides or contract patches.

**ArmSpec** — An experiment variant: input overrides and/or a contract patch.
- `description`: text
- `inputs`: object
- `patch`: object — Deep-merged into the contract (objects merge, lists replace).
