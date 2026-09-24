"""Mistakes a first-time user makes with the Python API raise the SDK's own errors, each with its fix."""
import pytest

import fg_env


def _game():
    return fg_env.new("duel")


@pytest.mark.parametrize("participants, path, fix", [
    ({"player": "rnd"}, "participants.player", "did you mean 'random'?"),
    ("idel", "participants", "did you mean 'idle'?"),
    ({"plyer": "random"}, "participants.plyer", "did you mean 'player'?"),
])
def test_an_unknown_participant_or_key_is_a_contract_error_with_a_fix(participants, path, fix):
    with pytest.raises(fg_env.ContractError) as info:
        fg_env.run(_game(), participants)
    [issue] = info.value.issues
    assert issue.path == path and fix in issue.fix, str(issue)


def test_an_unknown_policy_lists_the_declared_ones():
    with pytest.raises(fg_env.ContractError) as info:
        fg_env.run(fg_env.new("shop"), {"household": "policy:thrifty"})
    assert "unknown participant 'policy:thrifty'" in info.value.issues[0].message
    assert "policies: none" in info.value.issues[0].fix


@pytest.mark.parametrize("entity, stage, fix", [("nrth", None, "did you mean 'north'?"),
                                                ("north", "ply", "did you mean 'play'?")])
def test_preview_of_an_unknown_entity_or_stage_is_a_contract_error_with_a_fix(entity, stage, fix):
    env = fg_env.load(_game())
    with pytest.raises(fg_env.ContractError) as info:
        env.preview(entity, stage)
    assert fix in info.value.issues[0].fix


@pytest.mark.parametrize("source, message", [("missing.json", "file not found"), ("{bad", "not valid JSON")])
def test_check_reports_an_unreadable_contract_as_an_issue(tmp_path, monkeypatch, source, message):
    monkeypatch.chdir(tmp_path)
    [issue] = fg_env.check(source)
    assert issue.severity == "error" and message in issue.message


def test_cloning_an_engine_refuses_to_replace_a_file_and_says_how(tmp_path):
    path = tmp_path / "my_market.json"
    fg_env.engines.clone("retail", path)
    with pytest.raises(FileExistsError, match="overwrite=True"):
        fg_env.engines.clone("retail", path)
    assert fg_env.engines.clone("retail", path, overwrite=True) == path
