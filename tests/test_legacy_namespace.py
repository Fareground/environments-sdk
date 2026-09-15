"""The template API lives under fg_env.legacy and fg-env legacy; the top level is the Environment SDK only."""
import pytest

import fg_env
import fg_env.legacy as legacy
from fg_env.__main__ import main


def test_every_template_api_name_imports_from_fg_env_legacy():
    missing = [name for name in legacy.__all__ if not hasattr(legacy, name)]
    assert missing == []


def test_the_top_level_exports_no_template_api_name():
    assert set(fg_env.__all__) & set(legacy.__all__) == set()
    assert all(hasattr(fg_env, name) for name in fg_env.__all__)


def test_template_api_commands_run_under_fg_env_legacy(capsys):
    assert main(["legacy", "versions"]) == 0
    assert "current:" in capsys.readouterr().out


def test_template_api_commands_are_not_top_level_commands(capsys):
    with pytest.raises(SystemExit):
        main(["compile", "template.json"])
    assert "invalid choice: 'compile'" in capsys.readouterr().err
