<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Fareground/env-kernel/main/assets/wordmark-dark.svg" />
  <img src="https://raw.githubusercontent.com/Fareground/env-kernel/main/assets/wordmark.svg" alt="Fareground" width="320" />
</picture>

# fg-env

*The Environment SDK for agents — define an environment as one contract, the engine runs it.*

<p>
  <a href="https://github.com/Fareground/env-kernel/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Fareground/env-kernel/ci.yml?branch=main&style=flat-square&label=CI" /></a>
  <a href="https://pypi.org/project/fg-env/"><img alt="PyPI" src="https://img.shields.io/pypi/v/fg-env?style=flat-square" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11+-3b82f6?style=flat-square" />
  <img alt="Dependencies" src="https://img.shields.io/badge/deps-pydantic%20only-2dd4a7?style=flat-square" />
  <img alt="Engine" src="https://img.shields.io/badge/engine-deterministic-9b59b6?style=flat-square" />
</p>

</div>

---

## Overview

**fg-env** turns one JSON contract into a running environment for AI agents: a market, a
council, an exchange, a courtroom, an epidemic, a game. The contract declares the world, the
people, what agents can do, what they see, how the world moves on its own, and what is measured.
The engine builds the world, wakes agents, gives each a short plain-language picture with typed
tools, applies their actions atomically, runs events and physics, and returns typed outputs.

You write data, never engine code. The same contract runs with LLM agents, coded crowds, or both,
and a run is reproducible from its seed.

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
[agent-framework](https://github.com/Fareground/agent-sdk),
[agent-id](https://github.com/Fareground/agent-id),
[agent-memory](https://github.com/Fareground/agent-memory),
[agent-knowledge](https://github.com/Fareground/agent-knowledge) and
[agent-messaging](https://github.com/Fareground/agent-messaging).

## Install

```bash
pip install fg-env
```

Python ≥ 3.11. The only runtime dependency is `pydantic`.

## Quickstart

```python
import fg_env

contract = {
    "name": "Lemonade stand",
    "brief": {"situation": "Two kids sell lemonade on a hot day.",
              "rules": "Set your price each hour. Cheaper stands get more customers."},
    "clock": {"rounds": 5, "unit": "hour"},
    "types": {"seller": {"agent": True, "props": {"price": 1.0, "earned": 0}}},
    "entities": {"ana": {"type": "seller", "name": "Ana"}, "ben": {"type": "seller", "name": "Ben"}},
    "actions": {
        "set_price": {"by": "seller", "description": "Set your price for this hour.",
                      "params": {"price": {"type": "number", "min": 0.25, "max": 5}},
                      "do": ["$actor.price = $params.price"], "terminal": True},
    },
    "stages": [{"name": "pricing", "turns": "simultaneous"}],
    "events": [{"phase": "end", "each": "seller", "do": [
        "$share = (1 / $it.price) / $sum(seller, 1 / $it.price)",
        "$it.earned += $round(40 * $share) * $it.price"]}],
    "views": {"market": {"for": "seller", "title": "Stands", "of": "seller",
                         "show": "{name}: price {price|money}, earned {earned|money}"}},
    "outputs": {"winner": {"expr": "$top(seller, $it.earned, 1)[0].name", "type": "text"}},
}

result = fg_env.run(contract, seed=1)          # random agents; same seed, same run
print(result.summary())
print(result.outputs)                          # {'winner': 'Ben'} — typed, per the contract
```

What an agent receives on its turn:

```python
env = fg_env.load(contract, seed=1)
print(env.preview("ana"))    # {'brief': ..., 'update': ..., 'tools': [...], 'tokens': {...}}
```

Run it with an LLM — pass your own client:

```python
import anthropic
claude = fg_env.participants.anthropic(anthropic.Anthropic(), "claude-sonnet-5")
result = fg_env.run(contract, {"seller": claude}, seed=1)
```

Or with your own code. A participant is any function that takes a `Wake`:

```python
def cautious(wake):
    print(wake.update)                                   # the same picture an LLM reads
    result = wake.call("set_price", {"price": 9})        # out of range
    print(result.text)                                   # "set_price was not done: price must be at most 5 (got 9). ..."
    wake.call("set_price", {"price": max(0.25, wake.me["price"] - 0.25)})

fg_env.run(contract, {"ana": cautious, "ben": claude}, seed=1)
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

The complete authoring guide is generated from the SDK itself, so it always matches the engine:

```bash
fg-env guide            # or: python -c "import fg_env; print(fg_env.guide())"
```

## Tooling

```bash
fg-env check shop.json                    # every problem with its path and a fix, plus a smoke round
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

[`examples/contracts/`](https://github.com/Fareground/env-kernel/tree/main/examples/contracts) holds complete environments. Each was written by an LLM
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
commands under `fg-env legacy`; see [`docs/template_schema.md`](https://github.com/Fareground/env-kernel/blob/main/docs/template_schema.md).
New environments should use contracts.

## Contributing

See [CONTRIBUTING.md](https://github.com/Fareground/env-kernel/blob/main/CONTRIBUTING.md) for dev setup, tests and lint. What changed is in the
[CHANGELOG](https://github.com/Fareground/env-kernel/blob/main/CHANGELOG.md).

---

<div align="center">
<sub>Stewarded by <b>Fareground</b>.</sub><br />
<sub>Licensed under the <a href="https://github.com/Fareground/env-kernel/blob/main/LICENSE">Apache License 2.0</a>.</sub>
</div>
