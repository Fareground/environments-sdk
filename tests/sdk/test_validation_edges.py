"""Reserved-word names, refusals inside events, and arguments whose checks read an earlier bad argument."""
import fg_env

BASE = {"name": "Edges", "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "props": {"cash": 10}}},
        "entities": {"ann": {"type": "player"}},
        "stages": [{"name": "play", "turns": "sequential"}]}


def _contract(**sections):
    return {**BASE, **sections}


def test_reserved_word_names_are_errors_because_expressions_cannot_read_them():
    contract = _contract(types={"player": {"agent": True, "props": {"from": "", "cash": 10}}},
                         actions={"pay": {"by": "player", "params": {"in": {"type": "int"}}, "do": []}})
    errors = [str(i) for i in fg_env.check(contract) if i.severity == "error"]
    assert any("types.player.props.from" in e and "reserved word" in e for e in errors)
    assert any("actions.pay.params.in" in e and "reserved word" in e for e in errors)


def test_a_refusal_inside_an_event_is_logged_for_the_record_and_shown_to_no_agent():
    env = fg_env.load(_contract(events=[{"phase": "start", "do": ["$world.tick = 1", {"fail": "the vault is empty"}]}],
                                world={"tick": 0}), seed=1)
    result = env.run("idle")
    refused = [e for e in result.events if "was refused" in e.get("text", "")]
    assert refused and "the vault is empty" in refused[0]["text"]
    assert env.props["tick"] == 0  # the refused block was undone


def test_an_argument_whose_bound_reads_an_earlier_bad_argument_is_reported_not_crashed():
    contract = _contract(actions={"split": {"by": "player", "terminal": True, "params": {
        "total": {"type": "int", "min": 1, "max": "$actor.cash"},
        "part": {"type": "int", "min": 0, "max": "$params.total"}}, "do": []}})
    env = fg_env.load(contract, seed=1)
    told = []

    def play(wake):
        told.append(wake.call("split", {"total": "lots", "part": 3}).text)
        told.append(wake.call("split", {"total": 5, "part": 3}).text)
        wake.end()

    result = env.run(play)
    assert result.status == "completed", result.error
    assert "total must be a number" in told[0] and "part can be checked once total is corrected" in told[0]
    assert "not done" not in told[1]
