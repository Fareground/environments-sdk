# Environments SDK

**Describe the world. Define its rules. Run the scenario.**

The Environments SDK (`fg-env`) turns a JSON contract into a rounds-based simulation. Use it to model inventory, pricing, sales pipelines, capacity, negotiations, campaigns, or other interacting decisions. A human or an agent authors the contract; the engine executes it.

[Build your first environment →](getting-started.md) · [Engine starters](engines.md) · [Authoring workflow](authoring.md) · [API reference](api.md)

## One contract, one engine

A contract describes people and organizations, products and resources, available actions, information, timing, constraints, and outcomes. Participants can use language models, Python callables, or declarative policies. Use the same engine locally or within a host application.

| You need to… | Start here |
|---|---|
| Run a complete business example | [Quickstart](getting-started.md) |
| Clone an existing engine and sample personas | [Engine starters](engines.md) |
| Translate a scenario brief into rules | [Authoring scenarios](authoring.md) |
| Understand rounds, actions and state | [Core concepts](concepts.md) |
| Add demand, lead times, capacity or networks | [Business modeling](business-modeling.md) |
| Connect an agent or integrate a runner | [Participants and hosting](integration.md) |
| Compare decisions and inspect uncertainty | [Experiments and validation](validation.md) |
| Diagnose a failed or unfaithful simulation | [Troubleshooting](troubleshooting.md) |
| Look up an exact field or expression | [Contract reference](reference.md) |

## What makes a simulation useful

The engine enforces the rules you specify. Scenario quality also depends on choosing the right rules, realistic inputs, and meaningful tests. A contract that runs is not automatically a calibrated forecast. Keep assumptions explicit, compare against known outcomes, and report uncertainty.

## Install and compatibility

Python 3.11 or later. The core package depends on Pydantic; language-model clients are optional and supplied by the host.

<!-- not run: installs the package -->
```bash
python -m pip install fg-env
```

These pages track the contract API on `main`. Check the version you are actually
running with `python -c "import fg_env; print(fg_env.__version__)"`, and use the
[changelog](../../CHANGELOG.md) for release-specific behavior. The
[migration guide](migration.md) explains the public rename and the removed
template API.

[GitHub repository](https://github.com/Fareground/environments-sdk) · [PyPI](https://pypi.org/project/fg-env/) · [Apache-2.0 license](https://github.com/Fareground/environments-sdk/blob/main/LICENSE)
