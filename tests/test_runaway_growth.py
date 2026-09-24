"""A world cannot grow without bound: `check` warns when its smoke play grows the population fast enough to pass the
engine's ceiling before the run ends, and a run that reaches the ceiling fails there, saying how to bound it."""
import pytest

import fg_env
import fg_env.world


def spawning(rounds):
    """Every agent creates another agent each round: the population doubles every round."""
    return {"name": "Grow", "clock": {"rounds": rounds}, "types": {"p": {"agent": True}},
            "entities": {"a": {"type": "p"}}, "actions": {"spawn": {"by": "p", "do": [{"create": "p"}]}},
            "outputs": {"count": "$count(p)"}}


def making(rounds):
    """One agent makes one widget a round: the population grows by one a round."""
    return {"name": "Workshop", "clock": {"rounds": rounds},
            "types": {"maker": {"agent": True}, "widget": {"props": {"v": 1}}},
            "entities": {"m": {"type": "maker"}}, "actions": {"make": {"by": "maker", "do": [{"create": "widget"}]}},
            "outputs": {"widgets": "$count(widget)"}}


def growth_warnings(contract):
    return [issue for issue in fg_env.check(contract) if "ceiling" in issue.message]


def test_check_warns_when_agents_creating_agents_would_pass_the_ceiling_before_the_run_ends():
    [warning] = growth_warnings(spawning(30))
    assert warning.severity == "warning"
    assert warning.path == "types.p"
    assert "1,000,000" in warning.message and "of 30" in warning.message
    assert "if" in warning.fix and "$count(p)" in warning.fix


def test_check_stays_quiet_when_the_growth_ends_well_below_the_ceiling():
    assert growth_warnings(spawning(10)) == []  # 1,024 agents at the end
    assert growth_warnings(making(5000)) == []  # one more widget a round: about 5,000


def test_a_run_that_reaches_the_ceiling_fails_there_saying_how_to_bound_it(monkeypatch):
    monkeypatch.setattr(fg_env.world, "MAX_ENTITIES", 100)
    with pytest.raises(fg_env.RunError) as caught:
        fg_env.run(spawning(30), seed=1)
    message = str(caught.value)
    assert "actions.spawn" in message
    assert "100 living entities" in message and "$count(p)" in message
    assert caught.value.result.status == "failed"


def test_removed_entities_do_not_count_toward_the_ceiling(monkeypatch):
    monkeypatch.setattr(fg_env.world, "MAX_ENTITIES", 10)
    churn = making(40)
    churn["events"] = [{"phase": "end", "each": "widget", "do": [{"remove": "$it"}]}]
    assert fg_env.run(churn, seed=1).status == "completed"
