# fg-env documentation

## Environment SDK (contracts)

The contract SDK (`fg_env.load`, `fg_env.run`, `fg_env.check`, ...) is documented from the
SDK itself, so the docs always match the installed engine:

- **Authoring guide:** `fg-env guide` (or `python -c "import fg_env; print(fg_env.guide())"`).
  Everything needed to write a contract: sections, expression language, built-in functions,
  participants and tooling.
- **Contract JSON Schema:** `fg-env schema`. The committed copy lives at
  [`schema/contract.schema.json`](../schema/contract.schema.json).
- **Overview and quickstart:** the [project README](../README.md).
- **Example contracts:** [`examples/contracts/`](../examples/contracts/).
- **Security model:** [`SECURITY.md`](../SECURITY.md).

## Template API (legacy)

Every other file in this directory documents the older **template API** (`fg_env.legacy`: `Kernel`, `simulate`,
`load_world`; commands under `fg-env legacy`, like `fg-env legacy compile`). It remains supported for existing templates; new environments
should be written as contracts.

| Document | Covers |
| --- | --- |
| [`template_schema.md`](template_schema.md) | Template JSON reference |
| [`kernel_contract.json`](kernel_contract.json) | Template JSON Schema and live capabilities (`fg-env legacy contract`) |
| [`effect-values.md`](effect-values.md) | Typed effect values |
| [`conditional-effects.md`](conditional-effects.md) | Conditional effects |
| [`property-transfers.md`](property-transfers.md) | Property transfers |
| [`checkpoints.md`](checkpoints.md) | Execution checkpoints |
| [`checkpoint-history.md`](checkpoint-history.md) | Action history in checkpoints |
| [`snapshots.md`](snapshots.md) | World snapshots |
