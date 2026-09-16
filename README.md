<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/wordmark-dark.svg" />
    <img src="assets/wordmark.svg" alt="Fareground" width="320" />
  </picture>
</p>

<h1 align="center">Environments SDK</h1>

<p align="center">
  <em>Define an environment as one contract. The engine runs it.</em>
</p>

<p align="center">
  <a href="https://github.com/Fareground/environments-sdk/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Fareground/environments-sdk/ci.yml?branch=main&amp;style=flat-square&amp;label=CI" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/PyPI-publication%20pending-orange?style=flat-square" alt="PyPI publication pending" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-lightgrey" alt="Apache-2.0" /></a>
</p>

---

[Documentation](https://fareground.com/docs/env-kernel/) · [Quickstart](https://github.com/Fareground/environments-sdk/blob/d1e98a2/docs/sdk/getting-started.md) · [Authoring guide](https://github.com/Fareground/environments-sdk/blob/d1e98a2/docs/sdk/authoring.md) · [Reference](https://github.com/Fareground/environments-sdk/blob/d1e98a2/docs/sdk/reference.md)

## Overview

**fg-env** turns one JSON contract into a running environment for AI agents: a market, a
council, an exchange, a courtroom, an epidemic, a game. The contract declares the world, the
people, what agents can do, what they see, how the world moves on its own, and what is measured.
The engine builds the world, wakes agents, gives each a short plain-language picture with typed
tools, applies their actions atomically, runs scheduled world rules, and returns typed outputs.

You write data, never engine code. The same contract runs with LLM agents, coded crowds, or both,
and engine randomness is reproducible from its seed. Reproducing an LLM run also requires the same participant decisions; record traces for replay.

Built for LLM agents from the ground up:

- **A cacheable brief and a compact update.** Each turn opens with why the agent is acting,
  what changed since its last turn, and ranked views of the world, names first, with ids as
  handles. Nothing is repeated that the agent already has.
- **One typed tool per legal action.** JSON Schema with enums and numeric bounds. An invalid
  call returns exactly what to fix, and a refused action changes nothing.
- **Participant text stays marked.** Anything an agent writes carries its provenance through
  records, properties and views, and is always shown «quoted».
- **Measured.** Every run reports turns, tool calls, invalid calls and tokens per update.

fg-env is one of Fareground's open-source building blocks, alongside
[Agents SDK](https://github.com/Fareground/agents-sdk),
[agent-id](https://github.com/Fareground/agent-id),
[agent-memory](https://github.com/Fareground/agent-memory),
[agent-knowledge](https://github.com/Fareground/agent-knowledge) and
[agent-messaging](https://github.com/Fareground/agent-messaging).

## Install

The contract SDK described here is available from the tested development revision below. PyPI publication as `fg-env` is pending. The default branch still contains the older `fg-env-kernel` implementation until the SDK release is merged.

```bash
python -m pip install "git+https://github.com/Fareground/environments-sdk.git@d1e98a2"
```

The new distribution/import names are `fg-env` / `fg_env`. The older published distribution/import names are `fg-env-kernel` / `fg_env_kernel`; they are not interchangeable.

Python ≥ 3.11. The only runtime dependency is `pydantic`.

## Quickstart

For a business walkthrough, start with [weekly inventory](https://github.com/Fareground/environments-sdk/blob/d1e98a2/docs/sdk/getting-started.md), including exact expected outputs. The following small contract illustrates the basic API:

```python
import fg_env

contract = {
    "name": "Coin flip",
    "brief": {"rules": "Bet some coins each round. Heads you win that much, tails you lose it."},
    "types": {"player": {"agent": True, "props": {"coins": 10}}},
    "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}},
    "actions": {"bet": {"by": "player", "params": {"amount": {"type": "int", "min": 1, "max": "$actor.coins"}},
                        "do": "$actor.coins += $params.amount if $chance(0.5) else -$params.amount"}},
    "outputs": {"richest": "$best(player, $it.coins, 'random').name"},
}

print(fg_env.check(contract))        # [] — every problem would come with its path and a fix
result = fg_env.run(contract, seed=1)  # random agents; same seed, same run
print(result.outputs)                  # typed, per the contract
```

Or start from a template and read the short core guide:

```bash
fg-env new game my_game.json    # blank, game, market, simulation or social — checks clean and runs
fg-env check my_game.json       # static checks plus one played round
fg-env guide                    # the core guide; it maps every other part: fg-env guide actions, fg-env guide market.auction
```

What an agent receives on its turn:

```python
env = fg_env.load(contract, seed=1)
print(env.preview("ann"))    # {'brief': ..., 'update': ..., 'tools': [...], 'tokens': {...}}
```

Run it with an LLM — pass your own client:

```python
import anthropic
claude = fg_env.participants.anthropic(anthropic.Anthropic(), "YOUR_AVAILABLE_MODEL_ID")
result = fg_env.run(contract, {"player": claude}, seed=1)
```

Or with your own code. A participant is any function that takes a `Wake`:

```python
def cautious(wake):
    print(wake.update)                                   # the same picture an LLM reads
    result = wake.call("bet", {"amount": 99})            # out of range
    print(result.text)                                   # "bet was not done: amount must be at most 10 (got 99). ..."
    wake.call("bet", {"amount": 1})

fg_env.run(contract, {"ann": cautious, "bob": claude}, seed=1)
```

## The contract

| Section | What it declares |
|---|---|
| `inputs` | Typed values supplied at load (numbers, enums, dates, tables of rows) |
| `brief` | Static text: situation, rules, role text per agent type |
| `clock`, `space` | Round budget and calendar; grid, graph or plane positions |
| `world`, `types`, `entities`, `population` | Global props; kinds of entities with inheritance; named entities; sampled populations |
| `relations`, `links` | Typed links and generated networks (small-world, random, ring, complete) |
| `physics` | Continuous variables integrated with RK4, read from and written back to the world |
| `records` | Append-only logs (chat, reviews, transcripts) with per-viewer visibility |
| `actions` | What agents can do: typed params, requirements, chance, atomic effects, outcome text |
| `stages` | The steps of each round: sequential or sealed simultaneous turns, `until`, `quiet` |
| `views` | Ranked, filtered, templated slices of the world agents read |
| `events` | Scheduled, periodic, conditional or random world logic; shocks per experiment arm |
| `policies` | Coded participants as rules, for crowds and baselines |
| `metrics`, `outputs` | Series tracked each round; the typed result of a run |
| `end`, `invariants` | Early ending; rules that must always hold (a violation fails the run) |
| `arms`, `defs`, `blocks` | Experiment variants; reusable expressions and effect lists |

One small, strict expression language is used everywhere:
`$actor.cash >= $params.qty * $params.offer.price`, `$count(buyer, $it.cash > 0)`,
`$top(offer, [$it.rating, -$it.price], 5)`. Unknown properties and type errors are reported with
the fix; nothing silently evaluates to zero.

The authoring guide is generated from the SDK itself, so it always matches the engine. `fg-env guide` prints the
short core guide (enough for a first environment) with a map of every other part; `fg-env guide all` prints everything:

```bash
fg-env guide              # or: python -c "import fg_env; print(fg_env.guide())"
fg-env guide stages       # one section's fields and the $roots available there
fg-env guide market       # a mechanism family; fg-env guide market.auction for one mode
```

## Tooling

```bash
fg-env check shop.json                    # every problem with its path and a fix, plus a smoke round
fg-env expand shop.json --mechanisms       # the contract with every mechanism expanded into plain sections
fg-env preview shop.json shopper_1        # exactly what that agent reads, its tools, token estimates
fg-env preview shop.json shopper_1 --rounds 5 --agent shopper=policy:thrifty
fg-env run shop.json --seed 1 --input budget=50 --agent shopper=policy:thrifty --json
fg-env experiment shop.json --runs 20 --arms control,promo
fg-env schema                             # JSON Schema of the contract
```

In Python: `fg_env.check`, `fg_env.load`, `env.run` / `env.step`, `env.preview`,
`env.snapshot()` / `fg_env.Env.restore`, and `fg_env.experiment`. Experiment arms share seeds run
by run, so differences between arms come from the arm, not from luck.

## Examples

[`examples/contracts/`](https://github.com/Fareground/environments-sdk/tree/d1e98a2/examples/contracts) holds complete environments. Each was written by an LLM
agent from the guide alone, and each is covered by a golden-run test: a coffee market with sampled
households and subscriptions, a forecasting council, a price-time-priority order-book exchange, a
civil trial, a town epidemic with physics, Werewolf, a labor negotiation, Connect Four, a
Hold'em-lite poker table, the beer distribution game, a climate club with a CO2 model, a
ride-hailing city, checkers, a Diplomacy-style strategy game, and misinformation spreading on a follower network.

```bash
fg-env run examples/contracts/werewolf.json --seed 3
```

## Determinism

Every random draw comes from one seed tree per run, so a run is reproducible exactly from its seed.
Adding an event with a chance roll never changes how the population was sampled. Runs snapshot to
JSON between rounds and resume identically.

## Template API

The earlier template-based engine API (`Kernel`, `simulate`, `load_world`, the registry decorators)
remains available for existing templates as `fg_env.legacy` (`from fg_env.legacy import simulate`), with its
commands under `fg-env legacy`; see [`docs/template_schema.md`](https://github.com/Fareground/environments-sdk/blob/main/docs/template_schema.md).
New environments should use contracts.

## Contributing

See [CONTRIBUTING.md](https://github.com/Fareground/environments-sdk/blob/main/CONTRIBUTING.md) for dev setup, tests and lint. What changed is in the
[CHANGELOG](https://github.com/Fareground/environments-sdk/blob/main/CHANGELOG.md).

---

<div align="center">
<sub>Stewarded by <b>Fareground</b>.</sub><br />
<sub>Licensed under the <a href="https://github.com/Fareground/environments-sdk/blob/main/LICENSE">Apache License 2.0</a>.</sub>
</div>
