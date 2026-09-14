# Examples

## Contracts

Complete environments in [`contracts/`](contracts/). Each one checks clean, runs, and is covered by a
golden-run test (`tests/sdk/test_examples.py`). All except the lemonade stand were written by LLM
agents using only `fg-env guide`, as a stress test of the SDK.

| Contract | What it exercises |
|---|---|
| `lemonade_stand.json` | The minimal contract from the guide: simultaneous pricing, an end-of-round event, typed outputs |
| `coffee_market.json` | Households sampled from a table input, a neighbourhood map, cafés with capacity and subscriptions, reviews, a network, an arm that opens a new chain |
| `forecast_council.json` | Private information, sealed ballots, a chat deliberation until everyone is ready, aggregate outputs |
| `order_book_exchange.json` | Orders as entities, price-time priority matching with partial fills, reserves, fees, a news shock, a circuit breaker, conservation invariants |
| `civil_trial.json` | Roles, a phase state machine, exhibits with hidden strength, objections and rulings, a jury room hidden from counsel, secret ballots |
| `town_epidemic.json` | 300 residents on a small-world network, contagion events, physics for hospital load and immunity, a mayor with a budget, early vs late lockdown arms |
| `werewolf.json` | Hidden roles dealt from a shuffled deck, private night actions, a public day vote with ties |
| `labor_negotiation.json` | Alternating offers over three issues, private reservation values, strikes and lockouts, a mediator, a deadline |
| `connect_four.json` | A 6×7 board rendered as text rows, gravity drops, win detection in four directions |
| `holdem_lite.json` | A shuffled deck, private hole cards, blinds, one betting round with side pots, hand ranking at showdown |

```bash
fg-env check contracts/werewolf.json
fg-env preview contracts/werewolf.json p1
fg-env run contracts/werewolf.json --seed 3 --events
fg-env experiment contracts/town_epidemic.json --runs 5
```

## Template API scripts

`00_simulate.py`, `quickstart.py` and `tic_tac_toe/template.json` use the earlier template-based API
(`simulate`, `Kernel`), which remains available for existing templates:

```bash
PYTHONPATH=src python3 examples/00_simulate.py
PYTHONPATH=src python3 examples/quickstart.py
```
