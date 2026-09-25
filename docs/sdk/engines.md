# Engines

An engine is a complete, larger contract for one kind of human interaction (a market, a trial, a vote …), with coded
participants, that runs as cloned: copy the closest one and make it yours (its topic, roles, people, information,
rules and measurements). Where a cookbook recipe (`guide('cookbook')`) shows one pattern in a page, an engine is a
worked-out environment. `fg-env engines` lists them; `fg-env new --engine <id> my_env.json` clones one with its
bundled files (`fg_env.engines.clone`). Every engine plays with its coded policies alone, so a run without language
models is already a real simulation, and refuses a setup it cannot honour with the fix.

```python
import fg_env

for engine in fg_env.engines.list_engines(available=True):
    print(engine.id, "—", engine.summary)
path = fg_env.engines.clone("negotiation", "wage_talks.json", name="Wage talks")
print(fg_env.run(path, seed=1).summary())
```

`engine.materialized_source()` inlines an engine's bundled imports and data files into one contract, for a builder
that stores contracts in a database.

**People.** `fg_env.personas` samples a cohort from a population table before a run; the contract still decides what
those people know, want and may do. `sample_records(population, size=40, seed=11, constraints={...}, fixed=[...],
group_by="household_id")` draws with provenance (source, selection rules, seed, ids), `resample=False` repeats a
cohort and a new `run` draws a fresh one; `assign_labels(records, [("consumer", 0.8), ("seller", 0.2)], seed=11)`
gives each person a role. Sampling does not invent missing attributes or prove that a synthetic population predicts
real people.

## `retail` — Retail

Simulates households choosing each day whether to buy (against making coffee at home, so total demand answers to price) and at which café, while sellers set prices, promotions and subscriptions (coded cafés reprice toward more profit); the sampled households (at least 80) stand for the whole city, so capacities scale with the sample and a bigger sample is a finer estimate of the same market.

Clone: `fg-env new --engine retail my_env.json`. Roles: `household` (policies: `habit`, `hold`), `cafe` (policies: `hold`, `yield_manager`, `defender`).
Inputs: `households`, `cafes`, `sample_size`, `days`, `launch_day`, `launch_promo`, `launch_promo_days`, `review_rate`.

## `council` — Council

Simulates a panel (a table input) giving sealed probability forecasts on a yes/no question, deliberating and forecasting again; scores the final forecasts (Brier) once the outcome is supplied and measures consensus by their spread; coded panelists forecast from their prior with private noise and, having heard everyone, move part of the way toward the others by a personal openness.

Clone: `fg-env new --engine council my_env.json`. Roles: `panelist` (policies: `anchored`, `averager`).
Inputs: `question`, `briefing`, `panel`, `outcome`, `consensus_within`.

## `dispute` — Dispute

Simulates two sides offering (coded counsel lead with their strongest exhibits and now and then risk a flawed one), objecting to and cross-examining evidence before a judge; the parties, case, jury and exhibits are inputs; jurors weigh the total strength of what was admitted, sway one another in the jury room and vote on liability and damages, re-deliberating until a verdict or a hung jury; a jury-room remark pulls toward what a bound judge host ('judge') reads in its words, and without one toward the speaker's own leaning, so an unjudged remark's words move nobody.

Clone: `fg-env new --engine dispute my_env.json`. Roles: `attorney` (policies: `advocate`), `judge` (policies: `by_the_book`), `juror` (policies: `evidence_weigher`).
Inputs: `plaintiff`, `defendant`, `case`, `claimed_damages`, `evidence_rounds`, `votes_required`, `max_ballots`, `persuasion`, `jurors`, `exhibits`.

## `exchange` — Exchange

Simulates a stock exchange calibrated to a seed history: a coded crowd of market makers, trend, value, noise and passive traders trades one instrument on a limit order book while a few trader seats, filled by participants or a coded stand-in, decide between bars; news moves a hidden fundamental (by default a scripted guidance cut at bar 30 knocks 8% off it, so a default session ends lower).

Clone: `fg-env new --engine exchange my_env.json`. Roles: `trader`, `book_seed`, `seat` (policies: `seat_trend`).
Inputs: `history`, `seed_bars`, `bars`, `substeps`, `participants`, `population_preset`, `presets`, `activity`, `median_capital`, `circuit_breaker_pct`, `volatility_scale`, `drift_pct_per_bar`, `jump_prob_bar`, `jump_sigma`, `flow_scale`, `volatility_gain`, `events`, `seats`, `seat_cash`, `decision_every_bars`.

## `legislature` — Legislature

Simulates a legislative body moving, seconding, debating and voting on a measure (members may amend it; the coded members do not); each member's view blends their starting stance with the floor speeches so far, their own party's counting more, and the outcome is passed, rejected, or the status quo when nothing comes to a vote; a speech counts for what a bound judge host ('judge') reads in its words, and without one for the speaker's own stance, so an unjudged speech's words move nobody.

Clone: `fg-env new --engine legislature my_env.json`. Roles: `member` (policies: `member_baseline`), `presiding_officer` (policies: `chair_baseline`).
Inputs: `body_name`, `bill`, `persuasion`, `party_loyalty`, `members`.

## `contest` — Contest

Simulates participants submitting each round while a host judge scores them on a weighted rubric; the single top scorer wins, a rubric tie goes to the stronger hidden performance (skill plus luck), and without a judge skill and luck decide and the run says so (judged is false).

Clone: `fg-env new --engine contest my_env.json`. Roles: `contestant` (policies: `baseline`).
Inputs: `prompt`, `rounds`, `luck`, `participants`.

## `deliberation` — Deliberation

Simulates people exchanging reasons, proposing a conclusion (participants may amend it; the coded members do not) and voting; each member's view blends their starting stance with the speeches so far, and the outcome is passed, rejected, or the status quo when nothing comes to a vote; a speech counts for what a bound judge host ('judge') reads in its words, and without one for the speaker's own stance, so an unjudged speech's words move nobody.

Clone: `fg-env new --engine deliberation my_env.json`. Roles: `member` (policies: `baseline`).
Inputs: `question`, `persuasion`, `participants`.

## `negotiation` — Negotiation

Simulates two parties exchanging complete multi-issue offers and counteroffers before a deadline; nobody can offer or accept terms worth less to them than their private walk-away value, and each party's surplus is reported; coded parties concede toward the other side as the deadline nears, each at a pace drawn every run, the issues they care least about first.

Clone: `fg-env new --engine negotiation my_env.json`. Roles: `party` (policies: `concession`).
Inputs: `deadline`, `participants`.

## `population` — Population

Runs sampled people through a shared situation: each responds independently from their inclination, blurred by their uncertainty; the less confident answer undecided more often, and the responses are aggregated.

Clone: `fg-env new --engine population my_env.json`. Roles: `person` (policies: `baseline`).
Inputs: `question`, `participants`.

## `network` — Network

Simulates an idea spreading over explicit ties from the initial adopters (inputs.seeds): each round every adopter may convince each contact with a chance of trust times receptivity, so trust and time drive adoption; people may also take up or turn down what they heard, or recommend it; adopters, the seed included, stay committed.

Clone: `fg-env new --engine network my_env.json`. Roles: `person` (policies: `word_of_mouth`).
Inputs: `rounds`, `seeds`, `participants`, `ties`.

## `matching` — Matching

Simulates two-sided matching: applicants and selectors privately rank each other and deferred acceptance makes a stable match within each selector's capacity.

Clone: `fg-env new --engine matching my_env.json`. Roles: `applicant` (policies: `applicant_baseline`), `selector` (policies: `selector_baseline`).
Inputs: `applicants`, `selectors`, `taste`.

## `strategy` — Strategy

Simulates repeated cooperate-or-compete choices among any number of players under a configurable payoff matrix; coded players follow classic strategies (tit for tat, grim trigger, always cooperate or compete, random), which ignore the payoffs, or look forward and weigh the payoffs against the rounds left, with occasional mistakes, and a tie names no winner.

Clone: `fg-env new --engine strategy my_env.json`. Roles: `strategist` (policies: `baseline`).
Inputs: `rounds`, `participants`, `mistakes`, `mutual_cooperate`, `mutual_compete`, `compete_bonus`, `cooperate_loss`.

## `supply_chain` — Supply chain

Simulates a four-tier supply chain (retailer, wholesaler, distributor, factory) in which each tier sees only its own stock, backlog and incoming orders and orders from the tier above, with order, shipping and brewing delays; customer demand, delays and costs are inputs. Coded tiers order what they were asked for plus part of the gap to their target stock (base-stock and pass-through policies are included), so a demand step ripples upstream as the bullwhip effect; sharing real demand with every tier (share_demand) damps it. Reports cost, the bullwhip ratio, backlog and how long the chain takes to settle.

Clone: `fg-env new --engine supply_chain my_env.json`. Roles: `tier` (policies: `passthrough`, `anchor`, `base_stock`), `retailer`, `wholesaler`, `distributor`, `factory`.
Inputs: `demand`, `weeks`, `order_delay`, `shipping_delay`, `brewing_delay`, `holding_cost`, `backlog_cost`, `initial_inventory`, `max_order`, `share_demand`, `stable_tolerance`, `stable_window`.

## `auction` — Auction

Simulates collectors with private valuations (drawn every run) bidding for three identical lots, with an experiment arm per format: sealed first-price (the baseline), Vickrey second-price, English, Dutch, uniform-price and a double auction against dealers; the number of collectors and their budget are inputs. Coded collectors shade first-price bids below their value, bid their value in a Vickrey auction, raise in an English one while the price is under their value and take a Dutch clock once it falls far enough, so more competition raises the price.

Clone: `fg-env new --engine auction my_env.json`. Roles: `collector` (policies: `collector`).
Inputs: `collectors`, `budget`.

## `contact_centre` — Contact centre

Simulates one day (08:00-20:00, half-hours) at a contact centre call by call: arrivals follow a base rate times day-of-week and time-of-day indices fitted from a call history (a bundled CSV input), handle times are lognormal around the history's average and callers give up after a patience calibrated to its abandonment; agents on duty per half-hour are an input (empty: a rule of 10% over the forecast workload plus two). Arms compare that rule with an optimised plan, a network outage and callbacks; more staff answers more calls sooner.

Clone: `fg-env new --engine contact_centre my_env.json`. Roles: none (no agents).
Inputs: `day`, `history`, `staffing`, `agent_cost`, `shrinkage`, `aht_sec`, `aht_cv`, `patience_sec`, `outage_at`, `outage_uplift`, `outage_uplift_estimate`, `outage_half_life`, `outage_patience_share`, `callbacks`, `callback_wait_sec`, `callback_accept`, `callback_reserve`, `parameter_uncertainty`, `weekday_profile`, `weekday_profile_se`, `hours_profile`, `hours_profile_se`, `calls_scale`, `calls_scale_se`.

## `ride_hailing` — Ride hailing

Simulates drivers serving ride requests on a city grid in five-minute steps: demand follows an hourly profile with rush hours downtown (a table input), riders drop out as prices rise, and the platform can price each zone by surge; fleet size, fares and pricing are inputs. Coded drivers take the nearest reachable request and chase surge zones when idle; arms compare no surge with surge on trips, waits, cancellations, driver earnings and utilisation. More drivers cut cancellations.

Clone: `fg-env new --engine ride_hailing my_env.json`. Roles: `driver` (policies: `surge_chaser`, `nearest_only`).
Inputs: `drivers`, `start_hour`, `hours`, `demand_profile`, `surge`, `surge_sensitivity`, `surge_cap`, `rider_price_sensitivity`, `base_fare`, `fare_per_cell`, `driver_share`, `pickup_radius`, `cancel_after_min`.

## `epidemic` — Epidemic

Simulates a respiratory outbreak in a town of residents on a small-world contact network (susceptible, infected, recovered or dead, with waning immunity), with hospital capacity, a mayor who imposes lockdowns and runs a budget-limited vaccination campaign, and residents who decide each day whether to comply; transmissibility, illness length, immunity, beds, budget and policy triggers are inputs. Coded residents comply more when neighbours are sick and less the longer a lockdown lasts; the coded mayor locks down once past a case threshold, lifts it below another after two weeks, and vaccinates from vaccine_day as far as the budget allows. Arms compare early and late lockdowns; higher transmissibility infects more people.

Clone: `fg-env new --engine epidemic my_env.json`. Roles: `mayor` (policies: `threshold_mayor`), `resident` (policies: `crowd`).
Inputs: `residents`, `seed_cases`, `transmissibility`, `illness_days`, `lockdown_contact_cut`, `natural_immunity`, `vaccine_immunity`, `immunity_half_life`, `hospital_beds`, `hospital_stay`, `overload_death_multiplier`, `budget`, `lockdown_cost_per_day`, `dose_cost`, `max_daily_doses`, `vaccine_day`, `lockdown_trigger`, `lift_below`, `lockdown_fatigue`.

## `hidden_roles` — Hidden roles

Simulates social deduction among the players (a table input): werewolves (their number an input), a seer, a doctor and villagers are dealt at random and hidden; each night the werewolves pick a victim, the seer inspects a player and the doctor protects one, and each day everyone talks in public chat and votes to exile someone. Coded werewolves kill and vote against villagers, a seer who found a werewolf sometimes names it and votes for it, and everyone else votes at random (coded villagers do not follow what is said); more werewolves win more often.

Clone: `fg-env new --engine hidden_roles my_env.json`. Roles: `player` (policies: `honest`).
Inputs: `players`, `werewolves`.

