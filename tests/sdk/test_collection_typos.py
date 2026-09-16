"""A misspelled type name as the collection of any items function — built-in or stdlib — is caught."""
import fg_env

CONTRACT = {"name": "Typos", "clock": {"rounds": 1},
            "types": {"player": {"agent": True, "props": {"cash": 10}}},
            "entities": {"ann": {"type": "player"}},
            "stages": [{"name": "play", "turns": "sequential"}]}


def _errors(expression):
    contract = {**CONTRACT, "metrics": {"spread": expression}}
    return [str(i) for i in fg_env.check(contract) if i.severity == "error"]


def test_a_misspelled_type_is_caught_in_builtin_and_stdlib_collection_functions():
    assert any("'playr' is not a declared type" in e for e in _errors("$sum(playr, $it.cash)"))
    assert any("'playr' is not a declared type" in e for e in _errors("$variance(playr, $it.cash)"))
    assert any("'playr' is not a declared type" in e for e in _errors("$first(playr).cash"))
    assert not _errors("$variance(player, $it.cash)")
