"""Clones and forks of a spatial run carry its position index and layers exactly."""
import fg_env

GRAZE = {
    "name": "Graze",
    "clock": {"rounds": 6},
    "space": {"grid": {"rows": 5, "cols": 5, "neighborhood": "moore", "torus": True}, "capacity": 2,
              "layers": {"grass": {"type": "int", "default": "$randint(0, 3)", "max": 3}}},
    "types": {"cow": {"agent": True, "props": {"fed": 0}}},
    "population": [{"type": "cow", "count": 6, "at": "[($i * 2) % 5, ($i * 3) % 5]"}],
    "actions": {"graze": {"by": "cow", "do": [
        {"move": "$actor", "to": "$choice($cells($actor))"},
        "$actor.fed += $layer(grass, $actor)",
        {"layer": "grass", "at": "$actor", "set": 0},
        {"if": "$chance(0.2)", "then": [{"fail": "The cow wandered back."}]}]}},
    "events": [{"phase": "end", "do": [{"layer": "grass", "set": "$value + 1"}]}],
    "metrics": {"crowded": "$count(cow, $len($near($it, 1)) > 1)", "grass": "$sum($cells(), $layer(grass, $it))"},
}


def _index_matches_a_scan(env):
    world = env.world
    geometry = world.space.geometry
    for cell in geometry.cells():
        found = [e.id for e in world.space.positions.near(cell, 1, None, None)]
        scanned = [e.id for e in world.entities.values()
                   if e.alive and e.location_id is not None and geometry.distance(cell, e.location_id) <= 1]
        assert found == scanned


def test_a_clone_between_rounds_continues_exactly_with_its_positions_and_layers():
    env = fg_env.load(GRAZE, seed=4)
    env.run("random", rounds=2)
    copy = env.clone()
    assert copy.world.space.layers.values == env.world.space.layers.values
    _index_matches_a_scan(copy)
    assert copy.run("random").to_dict() == env.run("random").to_dict()


def test_a_clone_part_way_through_a_round_continues_exactly():
    points = {"n": 0}

    def stop(_env):
        points["n"] += 1
        return points["n"] == 9

    env = fg_env.load(GRAZE, seed=6)
    env.run("random", stop=stop)
    assert env.status == "stopped"
    copy = env.clone()
    _index_matches_a_scan(copy)
    assert copy.world.space.layers.values == env.world.space.layers.values
    assert copy.run("random").to_dict() == env.run("random").to_dict()


def test_a_fork_starts_from_the_same_positions_and_layers_and_keeps_its_index_current():
    env = fg_env.load(GRAZE, seed=8)
    env.run("random", rounds=3)
    fork = env.fork(seed=99)
    assert fork.world.space.layers.values == env.world.space.layers.values
    assert [e.location_id for e in fork.world.entities.values()] == [e.location_id for e in env.world.entities.values()]
    result = fork.run("random")
    assert result.status == "completed", result.error
    _index_matches_a_scan(fork)
