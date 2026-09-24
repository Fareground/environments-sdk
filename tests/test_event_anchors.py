"""Events are the one construct for world logic outside turns: `on` says where in a run an event is considered, and
`when` whether it fires. Every anchor fires at its place, in the order the events are written."""
import pytest

import fg_env

AGENT = {"agent": True, "props": {"acted": 0}}


def _logged(events, stages=None, rounds=1, **extra):
    """The contract's world log after ``rounds`` rounds played by idle agents."""
    contract = {"name": "Anchors", "clock": {"rounds": rounds}, "world": {"log": {"type": "list", "default": []}},
                "types": {"p": AGENT}, "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"go": {"by": "p", "do": ["$actor.acted += 1", "$world.log += 'act'"]}},
                "stages": stages or [{"name": "play", "actions": ["go"]}], "events": events, **extra}
    env = fg_env.load(contract)
    result = env.run("idle")
    assert result.ok, result.error
    return env.world.props["log"]


def test_every_anchor_fires_at_its_place_in_the_round():
    log = _logged([
        {"on": "round.end", "do": ["$world.log += 'round end'"]},
        {"on": "stage.play.end", "do": ["$world.log += 'play end'"]},
        {"on": "stage.play.turn", "do": ["$world.log += 'turn ' + $actor.id"]},
        {"on": "stage.play.start", "do": ["$world.log += 'play start'"]},
        {"do": ["$world.log += 'round start'"]},
    ])
    assert log == ["round start", "play start", "turn a", "turn b", "play end", "round end"]


def test_events_on_one_anchor_fire_in_the_order_written():
    log = _logged([{"on": "round.end", "do": ["$world.log += 'first'"]},
                   {"on": "round.end", "do": ["$world.log += 'second'"]}])
    assert log == ["first", "second"]


def test_a_turn_event_knows_whether_the_agent_acted():
    """A forfeit or a default move: `$acted` is false for an agent that ended its turn without acting."""
    log = _logged([{"on": "stage.play.turn", "when": "not $acted", "do": ["$world.log += 'idle ' + $actor.id"]}])
    assert log == ["idle a", "idle b"]


def test_stage_events_fire_only_when_the_stage_runs():
    log = _logged([{"on": "stage.play.start", "do": ["$world.log += 'start'"]},
                   {"on": "stage.play.end", "do": ["$world.log += 'end'"]}],
                  stages=[{"name": "play", "when": "$round == 2", "actions": ["go"]}], rounds=2)
    assert log == ["start", "end"]


def test_when_schedules_an_event_on_given_rounds():
    log = _logged([{"when": "$round == 2", "do": ["$world.log += 'r' + $text($round)"]},
                   {"when": "$round % 2 == 1", "do": ["$world.log += 'odd'"]}], rounds=3)
    assert log == ["odd", "r2", "odd"]


def test_create_and_remove_events_run_inside_the_change_with_the_entity():
    contract = {"name": "Life", "clock": {"rounds": 1}, "world": {"born": 0, "gone": 0},
                "types": {"animal": {"props": {"tag": ""}}, "sheep": {"extends": "animal"}, "p": AGENT},
                "entities": {"p": {"type": "p"}},
                "events": [{"on": "create.sheep", "do": ["$it.tag = $it.tag + 'sheep'"]},
                           {"on": "create.animal", "do": ["$it.tag = 'animal '", "$world.born += 1"]},
                           {"on": "remove.animal", "do": ["$world.gone += 1"]},
                           {"do": [{"create": "sheep", "as": "made"}, {"remove": "$made"}]}]}
    env = fg_env.load(contract)
    assert env.run("idle").ok
    sheep = next(e for e in env.world.entities.values() if e.entity_type == "sheep")
    assert sheep.properties["tag"] == "animal sheep"  # the ancestor's event first
    assert env.world.props["born"] == 1 and env.world.props["gone"] == 1


def test_a_change_event_fires_the_moment_its_condition_becomes_true_and_rearms():
    contract = {"name": "Alarm", "clock": {"rounds": 4}, "world": {"level": 0, "alarms": 0},
                "types": {"p": AGENT}, "entities": {"p": {"type": "p"}},
                "events": [{"do": ["$world.level = 0 if $world.level >= 2 else $world.level + 1"]},
                           {"on": "change", "when": "$world.level >= 2", "do": ["$world.alarms += 1"]}]}
    env = fg_env.load(contract)
    assert env.run("idle").ok
    assert env.world.props["alarms"] == 1  # levels 1, 2 (fires), 0, 1


def test_a_round_event_whose_do_is_one_loop_gives_each_item_its_own_luck():
    """Adding an entity never shifts the draws of the others."""
    def draws(count):
        contract = {"name": "Luck", "clock": {"rounds": 1}, "types": {"p": AGENT, "cell": {"props": {"x": 0.0}}},
                    "entities": {"p": {"type": "p"}}, "population": [{"type": "cell", "count": count}],
                    "events": [{"do": [{"each": "cell", "do": ["$it.x = $uniform(0, 1)"]}]}]}
        env = fg_env.load(contract, seed=3)
        env.run("idle")
        return {e.id: e.properties["x"] for e in env.world.entities.values() if e.entity_type == "cell"}

    few, more = draws(3), draws(5)
    assert all(more[key] == value for key, value in few.items())


def test_a_failing_event_fails_the_run_with_its_path():
    contract = {"name": "Broken", "clock": {"rounds": 1}, "types": {"p": AGENT}, "entities": {"p": {"type": "p"}},
                "stages": [{"name": "play"}],
                "events": [{"on": "stage.play.end", "do": [{"fail": "no"}]}]}
    result = fg_env.load(contract).run("idle")
    assert result.status == "failed" and "events[0].do" in result.error


def test_sync_in_an_each_effect_reads_the_world_as_it_was():
    contract = {"name": "Swap", "clock": {"rounds": 1}, "types": {"p": AGENT, "c": {"props": {"v": 0}}},
                "entities": {"p": {"type": "p"}, "x": {"type": "c", "props": {"v": 1}},
                             "y": {"type": "c", "props": {"v": 2}}},
                "actions": {"swap": {"by": "p", "do": [{"each": "c", "sync": True, "do": [
                    "$it.v = $sum(c, $it.v) - $it.v"]}]}}}
    env = fg_env.load(contract)
    assert env.run(lambda wake: wake.call("swap", {})).ok
    assert env.world.entities["x"].properties["v"] == 2 and env.world.entities["y"].properties["v"] == 1


@pytest.mark.parametrize(("event", "message"), [
    ({"on": "stage.nope.end", "do": ["$world.n += 1"]}, "there is no stage 'nope'"),
    ({"on": "create.ghost", "do": ["$world.n += 1"]}, "ghost"),
    ({"on": "create.p", "once": True, "do": ["$world.n += 1"]}, "`once` does not apply"),
    ({"on": "stage.play.turn", "do": ["$world.n += $it.x"]}, "$it"),
])
def test_check_reports_an_event_on_something_that_is_not_there(event, message):
    contract = {"name": "Bad", "world": {"n": 0}, "types": {"p": AGENT}, "entities": {"p": {"type": "p"}},
                "stages": [{"name": "play"}], "events": [event]}
    errors = [str(i) for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert any(message in e for e in errors), errors


@pytest.mark.parametrize(("event", "message"), [
    ({"on": "sometime"}, "is not an anchor"),
    ({"on": "change", "do": []}, "needs a `when`"),
])
def test_an_event_needs_a_real_anchor(event, message):
    contract = {"name": "Bad", "types": {"p": AGENT}, "events": [event]}
    with pytest.raises(fg_env.ContractError) as caught:
        fg_env.parse(contract)
    assert message in str(caught.value)
