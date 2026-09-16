# Names, versions and migration

## Public names

| Surface | Current name |
|---|---|
| Product | Environments SDK |
| GitHub repository | `Fareground/environments-sdk` |
| Python distribution | `fg-env` |
| Python import | `fg_env` |
| CLI | `fg-env` |

The repository and product rename does not require changing Python imports or installation commands. Update Git remotes and links that point to `env-kernel`:

```bash
git remote set-url origin https://github.com/Fareground/environments-sdk.git
```

## Contract API versus legacy templates

New environments should use JSON contracts through `fg_env.check`, `fg_env.load` and `fg_env.run`. The older template engine remains under `fg_env.legacy`, with CLI commands under `fg-env legacy`. A legacy Python template is not automatically a JSON contract; migrating it requires mapping its rules and testing equivalent behavior.

## Release discipline

These pages describe 0.3.0. Pin the SDK version for deployed scenarios and store it with each run. Before an upgrade, run scenario acceptance cases, compare outputs, and test saved-state restoration. Do not assume snapshots are portable across all versions.

See the repository [changelog](https://github.com/Fareground/environments-sdk/blob/main/CHANGELOG.md) for release changes. Publishing a package and deploying a platform are separate operations; a new documentation page alone does not establish which version a host is running.
