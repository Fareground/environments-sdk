# operations

## Mechanism family `operations`

Service operations: customers arriving on channels and served by staffed server pools — contact centres, clinics, counters, repair crews — with queues, patience, callbacks and service levels.

Named the same in every mode:
- `unit`: the time unit of every duration and threshold (second, minute, hour)

Modes (`"kind": "operations", "mode": ...`; read one with `guide('operations.<mode>')`):
- `queue`: A service system played natively, interval by interval: customers arrive on each channel (a Poisson process at the interval's expected `arrivals`), are answered at once by a free server of a pool with the skill, or wait in line — by `priority`, then arrival — and give up when their `patience` runs out; `callback` offers customers facing a long wait a call back, served when nobody is waiting, and `retry` brings some who gave up back later.
