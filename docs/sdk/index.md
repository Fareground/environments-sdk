# Environments SDK

`fg-env` runs environments for AI agents. You write one JSON contract — who exists, what they can do, when, what each
one sees and what is measured — and the engine runs it, the same way for every seed.

<!-- not run: installs the package -->
```bash
python -m pip install fg-env
```

## One path

1. **[Start here](authoring.md)** (`fg-env guide authoring`): the write → check → preview → run loop, one worked
   contract with a known-answer test, and the ten concepts of the language in order. Give the same page to an
   authoring agent as its starting context.
2. **[Cookbook](cookbook.md)** (`fg-env guide cookbook`): a complete contract for each common pattern, with its known
   answer. `fg-env new <recipe> my_env.json` writes one to start from.
3. **[Reference](reference.md)** (`fg-env guide <part>`): every section, function, effect and mechanism, core and
   extended, generated from the code; running, inspecting and analysing runs; the [Python API](api.md).

**[Engines](engines.md)** are larger, worked-out environments (a retail market, a trial, an exchange, an epidemic …)
to clone when one is close to what you need. A contract written for an earlier release still loads; see
**[migration](migration.md)** to save it in the current form.

Every page here except this one and the migration page is generated from `fg_env.guide`, so the documentation, the
guide an agent reads and the engine cannot disagree.

## Running it inside an application

A host stores the contract and its data, chooses the participants, runs the environment and shows outputs and
traces. It also owns authentication, tenant boundaries, resource limits (`budget=` caps tokens, calls and time),
credentials and file access: the SDK is not a sandbox for arbitrary host code. Pin the SDK version for deployed
scenarios and keep it, with the exact contract and inputs, beside each run.

[GitHub](https://github.com/Fareground/environments-sdk) · [PyPI](https://pypi.org/project/fg-env/) ·
[Changelog](../../CHANGELOG.md) · [Apache-2.0](https://github.com/Fareground/environments-sdk/blob/main/LICENSE)
