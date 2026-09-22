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
  <a href="https://pypi.org/project/fg-env/"><img src="https://img.shields.io/pypi/v/fg-env?style=flat-square" alt="PyPI version" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-lightgrey" alt="Apache-2.0" /></a>
</p>

---

[Documentation](https://fareground.com/docs/env-kernel/) · [Quickstart](docs/sdk/getting-started.md) · [Authoring guide](docs/sdk/authoring.md) · [Reference](docs/sdk/reference.md)

## Overview

**fg-env** turns one JSON contract into a running environment for AI agents. The contract
declares the participants, roles, private and public information, legal actions, state
transitions, stopping conditions, and measurements. The runtime builds the world, gives each
agent an appropriate view and typed tools, applies actions atomically, and returns typed outputs.

Use this SDK when you need to simulate people interacting under explicit rules and run the same
scenario repeatedly. Start from one of twelve reusable behavioral engines—Market, Council,
Dispute, Exchange, Legislature, Contest, Deliberation, Negotiation, Population, Network,
Matching, or Strategy—then customize the topic, participants, rules, information, and outcomes.

Do not treat an engine as a finished scenario. Engines provide interaction mechanics; your
environment supplies the real-world question and assumptions. Named Arena games and physical,
spatial, logistics, or disease models are not part of the behavioral engine catalog.

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

Install Environments SDK from PyPI:

```bash
python -m pip install --upgrade fg-env
```

The PyPI badge at the top of this page shows the current released version. This README documents
the `main` branch; release-specific behavior is recorded in the [changelog](CHANGELOG.md).
Python 3.11 or newer is required. The only runtime dependency is `pydantic`.

## Quickstart

For a business walkthrough, start with [weekly inventory](docs/sdk/getting-started.md), including exact expected outputs. The following small contract illustrates the basic API:

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

## Start from a reusable engine

Discover a versioned engine, clone its starter, then customize the contract:

```python
import fg_env

for engine in fg_env.list_engines():
    print(engine.id, engine.status, engine.available)

fg_env.clone_engine("market", "my_market.json", name="My market study")
result = fg_env.experiment("my_market.json", runs=20, participants="random")
print(result.table())
```

The catalog contains reusable behavioral engines only—not finished environments,
scenario presets, or Arena games. All twelve engines are native, available, and cloneable.

Persona generation is shared infrastructure rather than an environment:

```python
cohort = fg_env.sample_records(
    people, size=100, seed=7, run=0, resample=True,
    constraints={"region": "north"}, group_by="household_id",
    source="survey-2026",
)
```

See [engine starters and persona sampling](docs/sdk/engines.md).

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

Start with `fg-env guide authoring`: a compact, executable path from configurable
objects and tables to decisions, rounds and known-answer checks. Hosts can put
`fg_env.guide("authoring")` directly in an authoring agent’s starting context.
Field references are generated from the installed SDK; `fg-env guide` maps the
full language and `fg-env guide all` prints the complete reference.

```bash
fg-env guide authoring    # or: python -c 'import fg_env; print(fg_env.guide("authoring"))'
fg-env guide              # full language map
fg-env guide stages       # one section's fields and the $roots available there
fg-env guide market       # a mechanism family; fg-env guide market.auction for one mode
```

## Tooling

```bash
fg-env check shop.json                    # every problem with its path and a fix, then plays it with random agents and each policy
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

[`examples/contracts/`](https://github.com/Fareground/environments-sdk/tree/main/examples/contracts)
holds complete contracts covered by golden-run tests. They demonstrate contract features and
lower-level mechanics; they are examples, not entries in the behavioral engine catalog. For new
human-behavior scenarios, begin with the closest engine starter and customize it rather than
copying a named example.

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

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full terms. Security issues should follow the
private reporting process in [SECURITY.md](SECURITY.md).

---

<div align="center">
<sub>Stewarded by <b>Fareground</b>.</sub><br />
<sub>Licensed under the <a href="https://github.com/Fareground/environments-sdk/blob/main/LICENSE">Apache License 2.0</a>.</sub>
</div>
