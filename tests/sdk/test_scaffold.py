import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.sdk.scaffold import TEMPLATES, new


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_every_template_checks_without_errors_and_runs(template):
    contract = new(template)
    assert [i for i in fg_env.check(contract) if i.severity == "error"] == []
    result = fg_env.run(contract, seed=1)
    assert result.ok and result.output_issues == [], result.summary()


def test_new_writes_the_file_named_after_it_and_keeps_an_existing_one(tmp_path):
    path = tmp_path / "harbour_town.json"
    contract = new("market", path)
    assert json.loads(path.read_text()) == contract and contract["name"] == "Harbour town"
    with pytest.raises(FileExistsError):
        new("game", path)
    assert new("game", path, overwrite=True, name="Stones")["name"] == "Stones"


def test_an_unknown_template_suggests_the_closest():
    with pytest.raises(fg_env.ContractError, match="did you mean 'market'"):
        new("markt")


def test_cli_new_writes_a_contract_that_checks(tmp_path, capsys):
    path = tmp_path / "duel.json"
    assert main(["new", "game", str(path)]) == 0
    assert "fg-env check" in capsys.readouterr().out
    assert main(["check", str(path)]) == 0
    assert "contract OK" in capsys.readouterr().out
    assert main(["new", "game", str(path)]) == 1
