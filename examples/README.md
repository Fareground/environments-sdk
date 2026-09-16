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
| `civil_trial.json` | A civil trial as a `flow.procedure` state machine: openings, direct and cross, exhibits with hidden strength, objections and rulings, a jury room hidden from counsel, secret ballots |
| `town_epidemic.json` | 300 residents on a small-world network, contagion events, physics for hospital load and immunity, a mayor with a budget, early vs late lockdown arms |
| `werewolf.json` | Hidden roles with `groups.roles` (a dealt deck, teams, private night actions, eliminations) and a public day vote with `decision.ballot` |
| `labor_negotiation.json` | Alternating offers over three issues, private reservation values, strikes and lockouts, a mediator, a deadline |
| `connect_four.json` | Connect Four with `game.board`: gravity drops, only legal moves offered, win detection in four directions |
| `holdem_lite.json` | A shuffled deck, private hole cards, blinds, one betting round with side pots, hand ranking at showdown |
| `beer_game.json` | The beer distribution game with `economy.supply_chain`: shipping and brewing delays, backlog costs, conservation, a shared-demand arm |
| `climate_club.json` | Six countries from a table input, a CO2 and temperature model, pledges vs actual cuts, a climate club with border tariffs |
| `ride_hailing.json` | 30 drivers on a 10×10 grid, hourly demand profiles, pickups and trips over time, cancellations, surge pricing per zone |
| `checkers.json` | English draughts with `game.board`: legal moves only, mandatory captures, multi-jumps within one turn, crowning, no-move loss, draw rule |
| `misinformation.json` | 200 accounts on a one-way follower network with influencers, ranked feeds, hidden false stories, a fact-checker with delayed research, moderation arms |
| `diplomacy.json` | Five nations on a province map, private letters and pledges, sealed simultaneous orders with supports, bounces, retreats and builds |

```bash
fg-env check contracts/werewolf.json
fg-env preview contracts/werewolf.json p1
fg-env run contracts/werewolf.json --seed 3 --events
fg-env experiment contracts/town_epidemic.json --runs 5
```

## Template API scripts

`00_simulate.py`, `quickstart.py` and `tic_tac_toe/template.json` use the earlier template-based API
(`fg_env.legacy`: `simulate`, `Kernel`), which remains available for existing templates:

```bash
PYTHONPATH=src python3 examples/00_simulate.py
PYTHONPATH=src python3 examples/quickstart.py
```
