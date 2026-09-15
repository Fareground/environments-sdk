"""Reference systems spanning native rigid mechanics and equation dynamics."""
import json
import math

import pytest

import fg_env

pytest.importorskip("mujoco")


def spring_contract(step=0.005):
    return {"name": "Equation spring drives rigid mass", "clock": {"rounds": 10},
            "types": {"marker": {}},
            "world": {"position": {"type": "list", "default": [1, 0, 0]},
                      "velocity": {"type": "list", "default": [0, 0, 0]}},
            "physics": {"dt": 0.1, "read": {"v": "$world.velocity[0]"},
                        "vars": {"force": {"start": -4, "rate": "-4*v"}},
                        "rigid": {"model": f'''<mujoco><option timestep="{step}" integrator="RK4" gravity="0 0 0"/>
                          <worldbody><body name="mass" pos="1 0 0"><freejoint/>
                          <geom type="sphere" size="0.1" mass="1"/></body></worldbody></mujoco>''',
                          "force": {"mass": ["$physics.force", 0, 0, 0, 0, 0]},
                          "write": {"world.position": "body.mass.position", "world.velocity": "body.mass.velocity"}}},
            "outputs": {"position": "$world.position[0]", "velocity": "$world.velocity[0]", "force": "$physics.force"}}


def test_spring_feedback_converges_at_second_order():
    errors = []
    for width in [0.01, 0.005, 0.0025]:
        result = fg_env.load(spring_contract(width), seed=7).run()
        assert result.status == "completed", result.error
        assert result.outputs["force"] == pytest.approx(-4*result.outputs["position"], abs=1e-10)
        errors.append(abs(result.outputs["position"]-math.cos(2)))
        assert result.outputs["velocity"] == pytest.approx(-2*math.sin(2), abs=0.0001)
    assert errors[0]/errors[1] == pytest.approx(4, rel=0.01)
    assert errors[1]/errors[2] == pytest.approx(4, rel=0.01)


def test_hybrid_json_snapshot_replays_exactly():
    contract = spring_contract()
    straight = fg_env.load(contract, seed=7).run().to_dict()
    env = fg_env.load(contract, seed=7)
    env.run(rounds=4)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_equation_initial_values_can_read_the_initial_rigid_observation():
    contract = spring_contract()
    contract["world"]["position"]["default"] = [0, 0, 0]
    contract["physics"]["vars"]["force"]["start"] = "-4*$world.position[0]"
    env = fg_env.load(contract, seed=7)
    assert env.world.physics.values["force"] == -4
    assert env.run().outputs["position"] == pytest.approx(math.cos(2), abs=1e-5)


def test_continuous_hybrid_clock_reaches_its_fractional_horizon():
    contract = spring_contract()
    contract["clock"] = {"mode": "continuous", "horizon": 0.137, "tick": 0.04}
    contract["physics"]["dt"] = 1
    result = fg_env.load(contract, seed=7).run()
    assert result.status == "completed", result.error
    assert result.time == 0.137
    assert result.outputs["position"] == pytest.approx(math.cos(2*0.137), abs=2e-6)


def test_native_measured_properties_cannot_have_two_physical_owners():
    contract = spring_contract()
    contract["physics"]["write"] = {"world.position": "0"}
    issues = fg_env.check(contract)
    assert any(i.severity == "error" and "also written" in i.message for i in issues)


def test_hybrid_noise_increments_are_independent_across_subintervals():
    import statistics

    contract = spring_contract(0.025)
    contract["clock"] = {"rounds": 1}
    contract["physics"]["read"] = {}
    contract["physics"]["vars"] = {"x": {"start": 0, "rate": "0", "noise": "1"}}
    contract["physics"]["rigid"]["force"] = {}
    contract["outputs"] = {"x": "$physics.x"}
    values = [fg_env.load(contract, seed=seed).run().outputs["x"] for seed in range(120)]
    assert statistics.variance(values) == pytest.approx(0.1, rel=0.3)


def test_hybrid_failure_restores_both_solvers_after_partial_progress():
    contract = spring_contract()
    contract["physics"]["vars"]["force"]["rate"] = "sqrt(0.006-t)"
    env = fg_env.load(contract, seed=7)
    native = env.world.rigid.snapshot()
    result = env.run()
    assert result.status == "failed"
    assert env.world.physics.values["force"] == -4
    assert env.world.physics.time == 0
    assert env.world.rigid.snapshot() == native
    assert env.props["position"] == [1.0, 0.0, 0.0]
