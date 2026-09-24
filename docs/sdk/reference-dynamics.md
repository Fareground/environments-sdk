# dynamics

## Mechanism family `dynamics`

Continuous change integrated every round, before agents act: world variables and entity properties that follow differential equations (RK4, with Euler–Maruyama noise), read as $physics.<name>.

Modes (`"kind": "dynamics", "mode": ...`; read one with `guide('dynamics.<mode>')`):
- `ode`: Continuous variables integrated every round before agents act (RK4, with Euler–Maruyama noise): world variables with a `rate` over variable, param and `read` names, and per-type entity dynamics (`per`); `write` copies results into props.
