"""The public API: a small core at ``fg_env``, everything else one subpackage away."""
import importlib
import subprocess
import sys

import pytest

import fg_env

CORE = {
    "__version__", "load", "run", "check", "parse", "expand", "migrate", "experiment", "fork", "Env", "Contract",
    "RunResult", "ExperimentResult", "Branch", "Wake", "ToolResult", "Issue", "ContractError", "InputError", "RunError",
    "InvariantViolation", "FatalRunError", "SnapshotError", "guide", "schema", "new", "author", "participants",
    "analysis", "rl", "engines", "personas",
}
SUBPACKAGES = ["analysis", "rl", "engines", "personas", "participants"]


def test_top_level_is_the_core():
    assert set(fg_env.__all__) == CORE


def test_every_public_name_resolves_and_is_listed():
    assert all(getattr(fg_env, name) is not None for name in fg_env.__all__)
    assert set(fg_env.__all__) - {"__version__"} <= set(dir(fg_env))


def test_importing_fg_env_loads_only_what_is_used():
    loaded = subprocess.run([sys.executable, "-c", "import sys, fg_env; print(sorted(sys.modules))"],
                            capture_output=True, text=True, check=True).stdout
    assert "'fg_env.expr'" in loaded  # the expression functions register at once
    assert not any(f"'fg_env.{part}'" in loaded for part in ("api", "checks", "mechanisms", "analysis", "rl"))


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_and_exports_resolve(name):
    module = importlib.import_module(f"fg_env.{name}")
    assert getattr(fg_env, name) is module
    for export in getattr(module, "__all__", []):
        assert hasattr(module, export), f"fg_env.{name}.{export}"


def test_moved_names_live_in_their_subpackage():
    from fg_env.analysis import decompose, describe, fit_patterns, optimise, report, sweep, trace
    from fg_env.personas import sample_records
    from fg_env.rl import Game, evaluate, game, gym, pettingzoo_aec, tournament

    assert all(callable(f) for f in (decompose, describe, fit_patterns, optimise, report, sweep, trace,
                                     sample_records, Game, evaluate, game, gym, pettingzoo_aec, tournament))
    # `fg_env.game` is the games subpackage behind `rl.game`, never a callable at the top level
    assert not hasattr(fg_env, "sweep") and not callable(getattr(fg_env, "game", None))
