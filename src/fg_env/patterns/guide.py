"""The guide's `patterns` part: how patterns work, a small example per group, and every kind's fields."""
from __future__ import annotations

import json
import types
import typing
from typing import Any

from pydantic_core import PydanticUndefined

from . import catalogue  # noqa: F401 — registers every kind
from .base import GROUPS, KINDS, PatternConfig

__all__ = ["patterns_page"]

_INTRO = """\
## `patterns`: {name: {kind, …}}

The world's own regularities, declared once like its physics and read anywhere as values: a season, a trend, how
demand answers price, a random walk, a draw per customer, noisy counts, an effect that carries over. Write
`$pattern.winter` to read one, `$pattern.price_effect($it.price)` to call one with its driver, and pass the key last
when it has keys: `$pattern.season($it.category)`, `$pattern.sales($mean, $it)`.

* Parameters are fixed for a run: numbers, or expressions over `$inputs`, `$key` (the key), `$row` (the key's
  `table` row) and draw patterns. Put a knob in `inputs` and sweeps, arms, sensitivity, calibration and forks
  change the pattern with it. Run-time values (a price, a stock level) are arguments or a memory `input`.
* Keys: `keys` (an entity type — keys are its ids —, a list, or an expression over `$inputs`) and/or `table` +
  `column` (one row of parameters per key: per-SKU bases, per-category profiles).
* Time: `t` counts clock units from round 1 (round 1 is t = 0; with unit day and step 7 round 2 is t = 7). With
  `clock.start`, yearly, weekly and daily positions follow the real calendar and times
  may be ISO dates. Random paths step once per round (or every `every` clock units).
* Randomness comes from the pattern's own stream (run seed, name, key): adding a pattern never shifts another draw,
  every arm sees the same paths, and a snapshot, clone or fork reads the same values. Observations use one uniform
  draw per key and round, so the same key in the same round always draws alike: pass a key per item.
* Memory patterns read an `input` from the running world and commit it at the end of every round (after end events,
  before metrics); their state is the world property `patterns_memory`.
* Every pattern also takes `description`, `unit`, `min`, `max` (clamp), `record` (a metric of the same name, so
  `$series.<name>` and calibration targets see it), `uncertainty` ({parameter: standard error}: each run draws the
  parameter once around its value) and `fit`.
* Fit from data: `fg_env.analysis.fit_patterns(contract, data_dir=...)` estimates each pattern with a `fit` block from its
  data and returns the contract with the estimates written back as inputs (plus their standard errors); see the
  estimators below. `fg_env.analysis.decompose(contract, "demand", key=...)` shows what each factor of a product or sum adds.
* State that agents and events change is not a pattern: keep it in props written by events or `physics`, which read
  patterns (`"$it.trust += $pattern.trust_noise($it)"`)."""  # noqa: E501 — guide text: each line is shown as written

_EXAMPLES = {
    "time": {"season": {"kind": "seasonal", "period": "year", "table": "$inputs.categories", "column": "category",
                        "profile": "$row.profile"},
             "growth": {"kind": "trend", "form": "exponential", "rate": "$inputs.growth"}},
    "random": {"fuel": {"kind": "mean_reversion", "mean": 3.4, "rate": 0.2, "sd": 0.15, "min": 0}},
    "response": {"price_effect": {"kind": "elasticity", "elasticity": "$inputs.elasticity", "reference": 24.99}},
    "population": {"patience": {"kind": "draw", "keys": "customer", "dist": "lognormal", "mu": 1.2, "sigma": 0.4}},
    "observation": {"sales": {"kind": "counts", "dist": "negative_binomial", "dispersion": 4}},
    "memory": {"ads": {"kind": "carryover", "input": "$world.ad_spend", "half_life": 2}},
    "composition": {"demand": {"kind": "product", "keys": "sku", "scale": "$row.base", "table": "$inputs.skus",
                               "column": "sku", "of": ["growth", {"pattern": "season", "key": "$row.category"}]}},
}

_READS = {
    "time": '"$pattern.demand($it) * $pattern.growth"',
    "random": '"$world.fuel_price = $pattern.fuel"',
    "response": '"$pattern.price_effect($it.price)"',
    "population": '"$chance(0.1 * $pattern.patience($it))"',
    "observation": '"$it.sold = $min($it.stock, $pattern.sales($mean, $it))"',
    "memory": '"$world.visits = 500 * (1 + 0.001 * $pattern.ads)"',
    "composition": '"$pattern.demand($it)"',
}

_COMMON = set(PatternConfig.model_fields)


def _type_name(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return " | ".join(str(v) for v in typing.get_args(annotation))
    if origin is list:
        return "list"
    if origin is dict:
        return "object"
    if origin in (typing.Union, types.UnionType):
        names = [_type_name(a) for a in typing.get_args(annotation) if a is not type(None)]
        return " | ".join(dict.fromkeys(names))
    plain = {float: "number", int: "int", str: "text", bool: "bool"}
    return plain.get(annotation, getattr(annotation, "__name__", "any"))


def _fields(model: Any) -> list[str]:
    lines = []
    for name, info in model.model_fields.items():
        if name in _COMMON:
            continue
        default = "" if info.default is PydanticUndefined or info.is_required() else \
            (f" = {json.dumps(info.default)}" if info.default is not None else "")
        required = " (required)" if info.is_required() else ""
        lines.append(f"- `{name}`: {_type_name(info.annotation)}{default}{required} — "
                     f"{info.description or ''}".rstrip(" —"))
    return lines


def _call(spec: Any) -> str:
    try:
        args = spec.arg_names(spec.model.model_validate(spec.example))
    except Exception:  # an example that needs more context still documents the kind
        args = spec.args if not callable(spec.args) else ()
    return (f"`$pattern.<name>({', '.join(args)}[, key])`" if args
            else "`$pattern.<name>` (keyed: `$pattern.<name>(key)`)")


_FITTING = """\
### Fitting and explaining

Add `fit` to a pattern — `{data, value, time, key, x, mean, censored, where, adjust, noise}` — and run
`fg_env.analysis.fit_patterns(contract, data_dir=...)`. Each fit reads its rows, estimates, and returns `result.contract` with
the estimates written back as inputs (`<pattern>_<parameter>`, or a `<pattern>_fit` table per key) plus their standard
errors, which become the pattern's `uncertainty` scaled by the input `parameter_uncertainty` (1 draws each run's
parameters around the estimates, 0 uses the estimates). `result.report()` says, per pattern, the method, rows, RMSE,
MAPE and R², what was estimated and what was assumed. `fit` blocks stay in the contract, so a refit is one call.

* One pattern at a time: trend (least squares; log-linear for exponential), seasonal (slot means over the overall
  mean; harmonic regression), calendar (regression on the share of days each effect matches), elasticity (log-log),
  promotion lift, counts dispersion (method of moments), random walk, mean reversion (AR(1)), autoregression, weather,
  draws (moments), carry-over (grid search), saturation, diffusion and hazards (search / maximum likelihood).
  `adjust: ["trend"]` divides the value by already-fitted patterns first.
* A `product` fits jointly — its base per key, its seasonal profiles and exponential trend, and every response named in
  `x` (a constant elasticity, an exponential promotion) — as one log-link count regression, so a promotion's lift is not
  mistaken for price response. `censored: "stockout"` marks rows where demand went unmet (sales capped by stock, so
  demand was more than what sold): they are fitted as censored (expectation–maximisation), not dropped. `noise: "sales"` estimates that counts pattern's
  dispersion around the fitted means.
* `fit` and `calibration` answer different questions. `fit` estimates parameters from recorded data, once, and
  writes them into the contract; the `calibration` section tunes inputs at every load so simulated outputs hit
  targets — for what only the simulation identifies. Never list a fitted input in `calibration.params` (the checker
  warns): the load would replace the estimate.
* Judge a fitted contract on history it did not see: `fg_env.analysis.validate(result.contract, cases, season=52, test=0.25)`.
  `result.priors` holds the number estimates as `{input: {dist: "normal", mean, sd}}` for `uncertainty=` on
  experiment, sweep, backtest and validate — pass the input `parameter_uncertainty: 0` with them, since the contract
  already draws every fitted parameter itself.
* `fg_env.analysis.decompose(contract, "demand", key="BRP-TOY-V")` shows every factor of a product or sum and what it adds,
  round by round; `fg_env.analysis.describe(contract)` lists every pattern in plain words."""  # noqa: E501 — guide text: each line is shown as written


def patterns_page() -> str:
    lines = [_INTRO, "", _FITTING, "", "### Groups", ""]
    for group, about in GROUPS.items():
        kinds = [name for name, spec in KINDS.items() if spec.group == group]
        example = json.dumps(_EXAMPLES[group], ensure_ascii=False)
        lines += [f"**{group}** — {about}: " + ", ".join(f"`{k}`" for k in kinds) + ".",
                  f"`\"patterns\": {example}` · read {_READS[group]}", ""]
    lines += ["### Kinds", ""]
    for group in GROUPS:
        for name, spec in KINDS.items():
            if spec.group != group:
                continue
            lines += [f"#### `{name}` ({group}, {spec.shape})", "", spec.doc, "", f"Read: {_call(spec)}. Example: "
                      f"`{json.dumps(spec.example, ensure_ascii=False)}`", *_fields(spec.model), ""]
    return "\n".join(lines).rstrip()
