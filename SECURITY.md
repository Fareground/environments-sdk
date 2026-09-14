# Security Policy

## Supported Versions

Only the latest minor release receives security fixes.

| Version | Supported |
| ------- | --------- |
| 0.3.x   | Yes       |
| < 0.3   | No        |

`fg-env-kernel` (the previous distribution name, 0.2.x and earlier) no longer
receives fixes. Move to `fg-env`; see the 0.3.0 migration notes in
[CHANGELOG.md](https://github.com/Fareground/env-kernel/blob/main/CHANGELOG.md).

## Security model of the contract SDK

A contract is data. `fg_env.load(contract)` is designed to be safe to call on a
contract you did not write, within the limits below.

- **Contracts cannot execute code.** Every expression in a contract (conditions,
  effects, views, metrics, outputs, physics bindings) is parsed with Python's
  `ast` module used purely as a grammar. Only whitelisted node types are
  interpreted, only built-in `$functions` can be called, and attribute names
  starting with `_` are rejected. Nothing is passed to `eval`, `exec` or
  `import`.
- **Expression size is capped.** Expression source length and parsed node
  count are bounded, and expression evaluation is subject to a work budget, so
  a hostile contract cannot make a single expression arbitrarily expensive.
- **Participant text is provenance-marked.** Free text written by participants
  (for example an LLM agent's message) is carried as untrusted data and
  rendered wrapped in `«»` wherever other agents read it, and the brief tells
  agents to treat it as information, never as instructions. This reduces
  prompt injection between agents; it cannot eliminate it. Treat any LLM
  participant's output as untrusted in your own code.
- **LLM participants use your client.** The built-in Anthropic and OpenAI
  participants take a client object you construct. `fg-env` does not read API
  keys, make network calls of its own, or add provider SDK dependencies.

Out of scope:

- **The template API** (`Kernel`, `simulate`, `load_world`) supports
  registered Python primitives, and `fg_env.discover()` / the
  `KERNEL_PRIMITIVES_DIR` environment variable import `.py` files from a
  directory. Only point these at code you trust.
- **Resource use across a whole run.** Round counts, population sizes and
  similar declared quantities are chosen by the contract author. Bound them
  (and run untrusted contracts in a process you can time out) when executing
  contracts from untrusted sources.

## Reporting a Vulnerability

Please report security issues privately to **sandro@corza.ai**.
Do not open a public GitHub issue for vulnerabilities.

Include a description of the issue, steps to reproduce, and the affected
version. You will receive an acknowledgement, and a fix or mitigation will
be coordinated with you before any public disclosure.
