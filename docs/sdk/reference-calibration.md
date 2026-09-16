# calibration

## `calibration`: CalibrationSpec

Inputs fitted by short pilot sessions every time the contract loads, reproducible from the session's seed; a load that sets a fitted input skips it (each load costs budget × runs pilot sessions).

**CalibrationSpec** — A quick pilot calibration run whenever the contract loads: inputs are fitted so short pilot sessions hit the
targets, and the session runs with the fitted values (``env.inputs``, ``result.inputs``; the fit is in
``env.calibration``). Deterministic given the session's seed. It costs ``budget × runs`` pilot sessions plus
``holdout`` at every load that does not set a fitted input itself — setting one (or sweeping it) skips it.

A pilot fit is only as steady as its pilots: a noisy target (a volatility over a few dozen bars) fitted with one
short pilot per point can land anywhere in the range, even on its bounds (check ``env.calibration``). Longer
pilots, more ``runs`` per point, a larger ``holdout`` and a range no wider than plausible make it reliable.
- `params`: object (required) — {input: {low?, high?, log?}}: number or int inputs to fit (the range defaults to the input's min and max).
- `targets`: object (required) — {output or metric: target} as fg_env.calibrate takes them; a number (or a stat target's `value`) may be an expression over $inputs and $world, read from the world this session builds.
- `inputs`: object — Inputs of the pilot sessions only, e.g. fewer bars; the session's own inputs apply underneath.
- `runs`: int = 2 — Pilot sessions per evaluated point.
- `budget`: int = 6 — Distinct points evaluated.
- `holdout`: int = 1 — Pilot sessions on fresh seeds that validate the fit.
- `method`: any = "auto" — Search method (see fg_env.calibrate).
- `workers`: int = 1 — Pilot sessions run in this many processes at once.
