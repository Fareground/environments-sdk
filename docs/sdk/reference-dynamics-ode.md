# dynamics / ode

### `dynamics.ode`
Continuous variables integrated every round before agents act (RK4, with Euler–Maruyama noise): world variables with a `rate` over variable, param and `read` names, and per-type entity dynamics (`per`); `write` copies results into props. Read as $physics.<name>. Name the mechanism `physics`.

Config:
- `dt` (default 1.0): Time integrated per round.
- `substeps` (default 4): Maximum drift step and noise interval: the round duration divided by this count. Drift refines further for accuracy.
- `noise_rtol` (default 0.01): Relative timestep convergence target for general noisy dynamics. Refinement reuses the same Brownian path.
- `rtol` (default 1e-07): Relative local error tolerance for continuous drift; smaller values request more precision.
- `atol` (default 1e-10): Absolute local drift error tolerance in variable units, important near zero.
- `params` (default {}): Constants (numbers or expressions over $inputs).
- `vars` (default {}): 
- `read` (default {}): Names read from the world during integration: {N: '$count(person)'}.
- `write` (default {}): After each step: {'world.price': 'P', 'person.risk': 'I/N'}.
- `per` (default {}): {type: EntityDynamics}: entity dynamics, including coupling through reads (viral load, firm capital, habit strength).

Nested config:
**PhysicsVar** — A continuous variable. With ``rate`` it is integrated (RK4): d(var)/dt = rate.
- `start`: any = 0 — Initial value (number or expression).
- `rate`: text — Math over variable/param names: 'beta*S*I/N'.
- `noise`: text — Stochastic term (Euler–Maruyama): d(var) = rate·dt + noise·dW, drawn from the run's seed; e.g. 'sigma*price'. Needs a rate; the var's min/max then hold at every sub-step.
- `min`: number
- `max`: number
**EntityDynamics** — Continuous dynamics of entities, jointly integrated when state is coupled.
Variables are number props visible to views, effects and snapshots; min/max
bounds apply at physical substeps.
- `where`: text — Which entities integrate this step ($it); the others keep their values.
- `params`: object — Constants for this type (numbers or expressions over $inputs, $world).
- `read`: object — Names read from shared intermediate entity state: {exposure: '$count($neighbors($it, contact), $it.sick)'}.
- `vars`: object — {number prop: EntityVar | rate}: the props integrated.
- `write`: object — After each step, other props of the entity from math: {'sick': 'viral_load > 5'}.
**EntityVar** — How one number property changes by itself on every entity. Shorthand: the rate text.
- `rate`: text (required) — d(prop)/dt as math over names: the entity's own number props, this type's params and reads, world physics variables and params, and t.
- `noise`: text — Stochastic term (Euler–Maruyama), drawn from the run's seed: d(prop) = rate·dt + noise·dW.

```json
{"world": {"infected": 0.0, "mixing": 1.0}, "types": {}, "mechanisms": {"physics": {"kind": "dynamics", "mode": "ode", "params": {"beta": 0.3, "gamma": 0.1}, "read": {"mixing": "$world.mixing"}, "vars": {"S": {"start": 990, "rate": "-beta*mixing*S*I/(S+I+R)", "min": 0}, "I": {"start": 10, "rate": "beta*mixing*S*I/(S+I+R) - gamma*I", "min": 0}, "R": {"start": 0, "rate": "gamma*I", "min": 0}}, "write": {"world.infected": "I"}}}}
```
