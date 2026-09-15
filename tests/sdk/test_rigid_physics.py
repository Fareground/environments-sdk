"""Rigid mechanics through the same public contract/run/snapshot interface."""
import copy
import json

import pytest

import fg_env

pytest.importorskip("mujoco")

FREE_FALL = """<mujoco><option timestep="0.001" integrator="RK4" gravity="0 0 -9.81"/>
<worldbody><body name="ball" pos="0 0 10"><freejoint/>
<geom type="sphere" size="0.1" mass="1"/></body></worldbody></mujoco>"""


def falling_contract():
    return {"name": "Falling body", "clock": {"rounds": 10},
            "types": {"marker": {}},
            "world": {"position": {"type": "list", "default": [0, 0, 0]},
                      "velocity": {"type": "list", "default": [0, 0, 0]}},
            "physics": {"dt": 0.1, "rigid": {"model": FREE_FALL,
                        "write": {"world.position": "body.ball.position", "world.velocity": "body.ball.velocity"}}},
            "outputs": {"position": "$world.position", "velocity": "$world.velocity"}}


def test_free_fall_matches_analytical_position_and_velocity():
    result = fg_env.load(falling_contract()).run()
    assert result.status == "completed", result.error
    assert result.outputs["position"] == pytest.approx([0, 0, 10-9.81/2], abs=1e-8)
    assert result.outputs["velocity"] == pytest.approx([0, 0, -9.81], abs=1e-8)


def test_full_rigid_state_survives_json_snapshot_replay():
    contract = falling_contract()
    straight = fg_env.load(contract, seed=7).run().to_dict()
    env = fg_env.load(contract, seed=7)
    env.run(rounds=4)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_ground_contact_supports_a_resting_body():
    contract = falling_contract()
    contract["clock"]["rounds"] = 40
    contract["physics"]["rigid"]["model"] = FREE_FALL.replace('integrator="RK4"', 'integrator="implicitfast"').replace(
        '<worldbody>', '<worldbody><geom name="floor" type="plane" size="10 10 0.1"/>').replace('pos="0 0 10"', 'pos="0 0 1"')
    result = fg_env.load(contract).run()
    assert result.status == "completed", result.error
    assert result.outputs["position"][2] == pytest.approx(0.1, abs=0.001)
    assert abs(result.outputs["velocity"][2]) < 0.001


def test_controlled_force_acts_through_the_rigid_engine():
    contract = falling_contract()
    contract["world"]["force"] = 2.0
    contract["physics"]["rigid"]["model"] = FREE_FALL.replace('gravity="0 0 -9.81"', 'gravity="0 0 0"')
    contract["physics"]["rigid"]["force"] = {"ball": ["$world.force", 0, 0, 0, 0, 0]}
    result = fg_env.load(contract).run()
    assert result.status == "completed", result.error
    assert result.outputs["position"][0] == pytest.approx(1, abs=1e-8)
    assert result.outputs["velocity"][0] == pytest.approx(2, abs=1e-8)


def test_external_model_files_are_rejected_before_compilation():
    contract = falling_contract()
    contract["physics"]["rigid"]["model"] = '<mujoco><include file="outside.xml"/></mujoco>'
    assert any(i.severity == "error" and i.path == "physics.rigid.model" for i in fg_env.check(contract))


def test_bad_force_restores_rigid_integration_state():
    contract = falling_contract()
    contract["world"]["number"] = 0.0
    # Valid initial scalar; a later invalid force causes the interval to fail.
    contract["physics"]["rigid"]["force"] = {"ball": ["$world.number", 0, 0, 0, 0, 0]}
    env = fg_env.load(contract)
    before = copy.deepcopy(env.world.rigid.snapshot())
    env.world.props["number"] = "invalid"
    result = env.run()
    assert result.status == "failed"
    assert env.world.rigid.snapshot() == before


def test_joint_actuator_matches_known_rotational_inertia():
    model = '''<mujoco><option timestep="0.0005" integrator="RK4" gravity="0 0 0"/>
    <worldbody><body name="rotor"><joint name="hinge" type="hinge" axis="0 0 1"/>
    <geom type="sphere" size="0.1" mass="1"/></body></worldbody>
    <actuator><motor name="drive" joint="hinge" gear="1"/></actuator></mujoco>'''
    contract = {"name": "Driven rotor", "clock": {"rounds": 10}, "types": {"marker": {}},
                "world": {"torque": 1.0, "angle": 0.0, "speed": 0.0},
                "physics": {"dt": 0.01, "rigid": {"model": model, "control": {"drive": "$world.torque"},
                    "write": {"world.angle": "joint.hinge.position", "world.speed": "joint.hinge.velocity"}}},
                "outputs": {"angle": "$world.angle", "speed": "$world.speed"}}
    result = fg_env.load(contract).run()
    assert result.status == "completed", result.error
    inertia = 2/5 * 0.1**2
    assert result.outputs["angle"] == pytest.approx(0.5/inertia*0.1**2, abs=1e-8)
    assert result.outputs["speed"] == pytest.approx(0.1/inertia, abs=1e-8)


def test_contact_warmstart_survives_snapshot_exactly():
    contract = falling_contract()
    contract["clock"]["rounds"] = 20
    contract["physics"]["rigid"]["model"] = FREE_FALL.replace('integrator="RK4"', 'integrator="implicitfast"').replace(
        '<worldbody>', '<worldbody><geom name="floor" type="plane" size="10 10 0.1"/>').replace('pos="0 0 10"', 'pos="0 0 1"')
    straight = fg_env.load(contract, seed=7).run().to_dict()
    env = fg_env.load(contract, seed=7)
    env.run(rounds=10)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_failed_observation_write_rolls_back_completed_native_steps():
    contract = falling_contract()
    contract["world"]["limited_time"] = {"type": "number", "default": 0.0, "max": 0.05}
    contract["physics"]["rigid"]["write"]["world.limited_time"] = "time"
    env = fg_env.load(contract)
    before = env.world.rigid.snapshot()
    result = env.run()
    assert result.status == "failed"
    assert env.world.rigid.snapshot() == before
    assert env.props["position"] == [0.0, 0.0, 10.0]


def test_rigid_snapshot_rejects_different_engine_version():
    env = fg_env.load(falling_contract())
    snapshot = env.snapshot()
    snapshot["rigid"]["engine_version"] = "different"
    with pytest.raises(Exception, match="same MuJoCo version"):
        fg_env.Env.restore(falling_contract(), snapshot)
