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
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+" />
  <a href="https://pypi.org/project/fg-env/"><img src="https://img.shields.io/pypi/v/fg-env?style=flat-square" alt="PyPI version" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-lightgrey" alt="Apache-2.0" /></a>
</p>

---

[Start here](docs/sdk/authoring.md) · [Cookbook](docs/sdk/cookbook.md) · [Engines](docs/sdk/engines.md) · [Reference](docs/sdk/reference.md) · [Documentation](https://fareground.com/docs/env-kernel/)

**fg-env** runs environments for AI agents. You write one JSON contract — who exists, what they can do, when, what
each one sees and what is measured — and the engine runs it: every agent gets a brief, an update and one typed tool
per legal action, and the run is reproducible under a seed.

## Install

<!-- not run: installs the package -->
```bash
python -m pip install --upgrade fg-env
```

Python 3.11 or newer; the only runtime dependency is `pydantic`. This README follows `main`; release notes are in
the [changelog](CHANGELOG.md).

## A contract that runs

Two players betting coins, ready to paste:

```python
import fg_env

contract = {
    "name": "Coin flip",
    "brief": {"rules": "Bet some coins each round. Heads you win that much, tails you lose it."},
    "clock": {"rounds": 5},
    "types": {"player": {"agent": True, "props": {"coins": 10}}},
    "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}},
    "actions": {"bet": {"by": "player", "description": "Bet coins on a coin flip.",
                        "params": {"amount": {"type": "int", "min": 1, "max": "$actor.coins"}},
                        "do": "$actor.coins += $params.amount if $chance(0.5) else -$params.amount"}},
    "outputs": {"coins": "$dict(player, $it.id, $it.coins)"},
}

print(fg_env.check(contract))                 # [] — every problem comes with its path and a fix
print(fg_env.load(contract).preview("ann"))   # exactly what ann reads, and her tools
result = fg_env.run(contract, seed=1)          # random agents; the same seed gives the same run
print(result.outputs)
```

A participant is any function that takes the agent's turn, or a language model:

```python
def cautious(wake):          # wake.update is the text an LLM would read this turn
    wake.call("bet", {"amount": 1})

print(fg_env.run(contract, {"ann": cautious}, seed=1).outputs)
# fg_env.run(contract, {"player": "anthropic:<model>"}) plays every player with a model (ANTHROPIC_API_KEY)
```

## Start here: `fg-env guide authoring`

```bash
fg-env guide authoring
```

One page, for people and for authoring agents alike: the write → check → preview → run loop, one worked contract
with a known-answer test, and the ten concepts of the language in order ([the same page here](docs/sdk/authoring.md)).
Then:

- **[Cookbook](docs/sdk/cookbook.md)** — complete contracts for common patterns: sealed-bid auction, vote,
  negotiation, hidden roles, market, queue, spreading on a network, board game, resource economy, grid, simulation
  over time. `fg-env new auction my_env.json` writes one to start from.
- **[Engines](docs/sdk/engines.md)** — eighteen larger, worked-out environments (retail, trial, exchange,
  legislature, epidemic …) with coded participants: `fg-env new --engine <id> my_env.json`.
- **[Reference](docs/sdk/reference.md)** — every section, function, effect and mechanism, generated from the code
  (`fg-env guide <part>`); the [Python API](docs/sdk/api.md).
- `fg-env author "<brief>" --model anthropic:<model> --out env.json` has a model do the loop for you.
- Written for an earlier release? `fg-env migrate env.json --write` saves it in the current form
  ([migration](docs/sdk/migration.md)).

## Determinism

Every random draw comes from one seed tree per run, each block of logic from its own stream keyed by where it is
written and the round. The same seed and contract give the same world draws whatever the participants choose, one
agent's actions never shift another's luck, and a refused action gives its draws back. Runs snapshot to JSON between
rounds and resume identically. Reproducing an LLM run also needs the same model decisions: record a trace
(`exposures=True`) to replay it.

[`examples/contracts/`](examples/README.md) holds more complete contracts, each pinned by a golden-run test.

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
