# Names, versions and migration

## Public names

| Surface | Current name |
|---|---|
| Product | Environments SDK |
| GitHub repository | `Fareground/environments-sdk` |
| Python distribution | `fg-env` |
| Python import | `fg_env` |
| CLI | `fg-env` |

For the current contract SDK, the repository and product rename does not require changing `fg_env` imports. The older published distribution was `fg-env-kernel` (0.2.x); the contract SDK is distributed as `fg-env` (0.3.x). This is a package migration, not an in-place rename of an existing PyPI project. Upgrade in a fresh virtual environment and verify your scenario tests. Update Git remotes and links that point to `env-kernel`:

<!-- not run: changes a git remote -->
```bash
git remote set-url origin https://github.com/Fareground/environments-sdk.git
```

Install the published distribution from PyPI:

<!-- not run: installs an older release -->
```bash
python -m pip install "fg-env==0.4.2"
```

## Legacy templates

Environments are JSON contracts run through `fg_env.check`, `fg_env.load` and `fg_env.run`. The older template engine (`fg_env.legacy`, `fg-env legacy`) was removed after 0.7; a project that still runs templates should pin `fg-env<0.8`. A template is not automatically a JSON contract; migrating it requires mapping its rules and testing equivalent behavior.

## Release discipline

These pages track the contract API on `main`. Pin the SDK version for deployed
scenarios and store it with each run. Before an upgrade, read the
[changelog](../../CHANGELOG.md), run scenario acceptance cases, compare
outputs, and test saved-state restoration. Do not assume snapshots are
portable across all versions.

See the repository [changelog](https://github.com/Fareground/environments-sdk/blob/main/CHANGELOG.md) for release changes. Publishing a package and deploying a platform are separate operations; a new documentation page alone does not establish which version a host is running.
