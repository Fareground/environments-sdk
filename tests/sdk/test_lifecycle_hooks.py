"""Entity lifecycle hooks: on_create and on_remove, inherited, atomic, at build and during the run."""
import copy
import json

import fg_env

FIRMS = {
    "name": "Firms",
    "clock": {"rounds": 3},
    "world": {"founded": 0, "closed": 0, "log": {"type": "list", "default": []}},
    "types": {
        "founder": {"agent": True},
        "firm": {"props": {"capital": 10, "seen_total": 0},
                 "on_create": ["$world.founded += 1", "$world.log += 'firm:' + $it.id",
                               "$it.seen_total = $count(firm)"],
                 "on_remove": ["$world.closed += 1", "$world.log += 'gone:' + $it.id + ':' + ('alive' if $it.alive else 'dead')"]},
        "bank": {"extends": "firm", "on_create": ["$world.log += 'bank:' + $it.id"]},
    },
    "entities": {"f0": {"type": "founder"}, "acme": {"type": "firm"}},
    "population": [{"type": "bank", "count": 2}],
    "actions": {
        "found": {"by": "founder", "do": [{"create": "firm", "as": "made"}]},
        "found_and_fail": {"by": "founder", "do": [{"create": "firm"}, {"fail": "The registry is closed."}]},
        "close": {"by": "founder", "params": {"firm": {"type": "entity", "of": "firm"}},
                  "do": [{"remove": "$params.firm"}, {"remove": "$params.firm"}]},
    },
    "stages": [{"name": "play", "max_actions": 3}],
    "outputs": {"founded": "$world.founded", "closed": "$world.closed"},
}


def _run(contract, calls, rounds=1, seed=1, expect_ok=True):
    env = fg_env.load(contract, seed=seed)

    def play(wake):
        for name, args in calls.get(wake.round, []):
            result = wake.call(name, args)
            assert result.ok or not expect_ok, (name, result.text)
        wake.end()

    env.run(play, rounds=rounds)
    return env


def test_build_runs_on_create_once_the_whole_world_exists_ancestors_first():
    env = fg_env.load(FIRMS, seed=1)
    assert env.props["founded"] == 3
    assert env.props["log"] == ["firm:acme", "firm:bank_1", "bank:bank_1", "firm:bank_2", "bank:bank_2"]
    assert {e["props"]["seen_total"] for e in env.entities("firm")} == {3}  # hooks saw the complete world


def test_created_and_removed_entities_run_their_hooks_inside_the_change():
    env = _run(FIRMS, {1: [("found", {}), ("close", {"firm": "acme"})]})
    assert env.props["founded"] == 4 and env.props["closed"] == 1  # a second remove of the same firm runs nothing
    assert env.props["log"][-2:] == ["firm:firm_1", "gone:acme:dead"]


def test_a_refused_action_takes_its_hooks_changes_back_and_a_hook_can_refuse_it():
    env = _run(FIRMS, {1: [("found_and_fail", {})]}, expect_ok=False)
    assert env.props["founded"] == 3 and env.entity("firm_1") is None
    strict = copy.deepcopy(FIRMS)
    strict["types"]["firm"]["on_create"].append({"if": "$count(firm) > 3", "then": [{"fail": "Too many firms."}]})
    outcomes = []
    env = fg_env.load(strict, seed=1)

    def play(wake):
        outcomes.append(wake.call("found", {}).text)
        wake.end()

    env.run(play, rounds=1)
    assert "Too many firms." in outcomes[0] and env.props["founded"] == 3


def test_on_create_at_build_false_runs_hooks_only_for_entities_created_during_the_run():
    contract = copy.deepcopy(FIRMS)
    contract["types"]["firm"]["on_create_at_build"] = False
    env = fg_env.load(contract, seed=1)
    assert env.props["founded"] == 0
    contract["types"]["bank"]["on_create_at_build"] = True  # the nearest declaration wins
    assert fg_env.load(contract, seed=1).props["log"] == ["firm:bank_1", "bank:bank_1", "firm:bank_2", "bank:bank_2"]
    env = _run(fg_env.load(contract, seed=1).contract, {1: [("found", {})]})
    assert env.props["founded"] == 3  # 2 banks at build + 1 firm during the run


def test_hooks_that_keep_creating_their_own_type_stop_with_a_clear_error():
    contract = copy.deepcopy(FIRMS)
    contract["types"]["firm"]["on_create"] = [{"create": "firm"}]
    contract["types"]["firm"]["on_create_at_build"] = False
    result = _run(contract, {1: [("found", {})]}, expect_ok=False).result()
    assert result.status == "failed"
    assert "on_create hooks set each other off more than 16 levels deep" in result.error


def test_a_run_split_by_a_snapshot_ends_exactly_like_a_straight_run():
    contract = copy.deepcopy(FIRMS)
    contract["events"] = [{"do": [{"create": "firm"}]}, {"every": 2, "do": [{"remove": "$entity(acme)"}]}]
    contract["types"]["firm"]["on_create"].append({"wake": "f0", "now": True, "why": "A firm opened."})
    straight = fg_env.load(contract, seed=3).run().to_dict()
    env = fg_env.load(contract, seed=3)
    env.run(rounds=1)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_the_checker_checks_hook_effects_over_it():
    contract = copy.deepcopy(FIRMS)
    contract["types"]["firm"]["on_create"] = ["$it.nope = 1", "$actor.capital = 1"]
    contract["types"]["founder"]["on_create_at_build"] = False
    issues = [(i.path, i.message, i.severity) for i in fg_env.check(contract)]
    assert ("types.firm.on_create[0]", "$it.nope: bank/firm has no property 'nope'", "error") in issues
    assert any(path == "types.firm.on_create[1]" and "$actor is not available" in message for path, message, _ in issues)
    assert ("types.founder.on_create_at_build", "does nothing: this type has no on_create", "warning") in issues
