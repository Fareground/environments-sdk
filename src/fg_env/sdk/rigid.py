"""MuJoCo rigid-body dynamics behind the SDK's clock, controls and snapshots."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, Dict

from .errors import RunError
from .expr import compile_expr, is_expr
from .props import prop_type

if TYPE_CHECKING:
    from .contract_rigid import RigidSpec
    from .world import SdkWorld


def validate_model(source: str) -> None:
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise RunError(f"invalid MJCF XML: {exc}", "physics.rigid.model") from None
    if root.tag != "mujoco":
        raise RunError("model root must be <mujoco>", "physics.rigid.model")
    for node in root.iter():
        if node.tag == "include" or "file" in node.attrib:
            raise RunError("rigid models must be self-contained; embed geometry instead of filesystem references", "physics.rigid.model")


class RigidDynamics:
    def __init__(self, world: "SdkWorld", spec: "RigidSpec"):
        validate_model(spec.model)
        try:
            import mujoco
            import numpy as np
        except ImportError:
            raise RunError("rigid physics needs the optional engine: install fg-env[physics]", "physics.rigid") from None
        self.mj, self.np, self.spec = mujoco, np, spec
        try:
            self.model = mujoco.MjModel.from_xml_string(spec.model)
            self.data = mujoco.MjData(self.model)
            if spec.keyframe is not None:
                mujoco.mj_resetDataKeyframe(self.model, self.data, self._id("key", spec.keyframe))
            mujoco.mj_forward(self.model, self.data)
            for name in spec.control:
                self._id("actuator", name)
            for name in spec.force:
                self._id("body", name)
            for source in spec.write.values():
                self.observe(source)
            self.write(world)
        except (ValueError, TypeError) as exc:
            raise RunError(str(exc), "physics.rigid.model") from None

    def _id(self, kind: str, name: str) -> int:
        obj = getattr(self.mj.mjtObj, f"mjOBJ_{kind.upper()}")
        index = self.mj.mj_name2id(self.model, obj, name)
        if index < 0:
            raise RunError(f"no {kind} named {name!r} in the rigid model", "physics.rigid")
        return index

    def _value(self, world: "SdkWorld", raw: Any) -> Any:
        if is_expr(raw):
            return compile_expr(raw)(world.scope())
        if isinstance(raw, list):
            return [self._value(world, item) for item in raw]
        return raw

    def step(self, world: "SdkWorld", dt: float) -> None:
        mj, model, data, np = self.mj, self.model, self.data, self.np
        for name, raw in self.spec.control.items():
            value = self._value(world, raw)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise RunError("control must be a finite number", f"physics.rigid.control.{name}")
            data.ctrl[self._id("actuator", name)] = value
        for name, raw in self.spec.force.items():
            values = self._value(world, raw)
            if not isinstance(values, list) or len(values) != 6 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise RunError("force must contain six finite numbers [fx,fy,fz,tx,ty,tz]", f"physics.rigid.force.{name}")
            data.xfrc_applied[self._id("body", name)] = values
        width = float(model.opt.timestep)
        if width <= 0 or not math.isfinite(width) or dt/width > 100_000:
            raise RunError("rigid interval exceeds 100000 steps; reduce physics.dt or the clock tick", "physics.rigid.model")
        initial = float(data.time)
        target = initial + dt
        warnings = np.array(data.warning.number, copy=True)
        try:
            while data.time < target:
                remaining = target-float(data.time)
                if remaining <= max(1e-15, dt*1e-12):
                    break
                model.opt.timestep = min(width, remaining)
                mj.mj_step(model, data)
                if not np.all(np.isfinite(data.qpos)) or not np.all(np.isfinite(data.qvel)) or np.any(data.warning.number > warnings):
                    raise RunError("rigid solver reported numerical trouble; reduce its timestep or revise the model", "physics.rigid")
            data.time = target
            mj.mj_forward(model, data)
            self.write(world)
        finally:
            model.opt.timestep = width

    def observe(self, source: str) -> Any:
        mj, m, d = self.mj, self.model, self.data
        if source == "time":
            return float(d.time)
        if source == "contacts":
            return int(d.ncon)
        if source == "energy":
            mj.mj_energyPos(m, d)
            mj.mj_energyVel(m, d)
            return [float(v) for v in d.energy]
        pieces = source.split(".")
        if len(pieces) == 2 and pieces[0] == "sensor":
            index = self._id("sensor", pieces[1])
            start, size = m.sensor_adr[index], m.sensor_dim[index]
            values = d.sensordata[start:start+size].tolist()
            return values[0] if size == 1 else values
        if len(pieces) != 3:
            raise RunError(f"unknown rigid observation {source!r}", "physics.rigid.write")
        kind, name, field = pieces
        if kind == "body":
            index = self._id("body", name)
            if field == "position":
                return d.xpos[index].tolist()
            if field == "quaternion":
                return d.xquat[index].tolist()
            if field in ("velocity", "angular_velocity"):
                values = self.np.zeros(6)
                mj.mj_objectVelocity(m, d, mj.mjtObj.mjOBJ_BODY, index, values, 0)
                return values[3:].tolist() if field == "velocity" else values[:3].tolist()
        if kind == "joint":
            index = self._id("joint", name)
            if field == "position":
                start = m.jnt_qposadr[index]
                end = m.jnt_qposadr[index+1] if index+1 < m.njnt else m.nq
                values = d.qpos[start:end].tolist()
                return values[0] if len(values) == 1 else values
            if field == "velocity":
                start = m.jnt_dofadr[index]
                end = m.jnt_dofadr[index+1] if index+1 < m.njnt else m.nv
                values = d.qvel[start:end].tolist()
                return values[0] if len(values) == 1 else values
        if kind == "actuator" and field == "force":
            return float(d.actuator_force[self._id("actuator", name)])
        raise RunError(f"unknown rigid observation {source!r}", "physics.rigid.write")

    def write(self, world: "SdkWorld") -> None:
        for target, source in self.spec.write.items():
            value = self.observe(source)
            parts = target.split(".")
            if len(parts) == 2 and parts[0] == "world" and parts[1] in world.contract.world:
                self._check_observation(world.contract.world[parts[1]], value, target)
                world.set_world(parts[1], value)
            elif len(parts) == 3 and parts[0] == "entity" and parts[1] in world.entities:
                entity = world.entities[parts[1]]
                self._check_observation(world.prop_spec(entity, parts[2]), value, target)
                world.set_prop(entity, parts[2], value)
            else:
                raise RunError(f"unknown observation target {target!r}", "physics.rigid.write")

    @staticmethod
    def _check_observation(spec: Any, value: Any, target: str) -> None:
        kind = prop_type(spec)
        if kind in ("number", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise RunError("observation requires a finite numeric target", f"physics.rigid.write.{target}")
            if kind == "int" and not float(value).is_integer():
                raise RunError("fractional observation needs a number property", f"physics.rigid.write.{target}")
            if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
                raise RunError("physical observation exceeds target bounds; expand the bounds rather than clipping measured state", f"physics.rigid.write.{target}")
        elif kind == "list" and not isinstance(value, list):
            raise RunError("scalar observation needs a numeric property", f"physics.rigid.write.{target}")
        elif kind not in ("number", "int", "list", "any"):
            raise RunError("physical observations need a number, int, list or any property", f"physics.rigid.write.{target}")
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise RunError("physical observation is non-finite", f"physics.rigid.write.{target}")

    def snapshot(self) -> Dict[str, Any]:
        kind = self.mj.mjtState.mjSTATE_INTEGRATION
        values = self.np.empty(self.mj.mj_stateSize(self.model, kind))
        self.mj.mj_getState(self.model, self.data, values, kind)
        return {"engine_version": self.mj.__version__, "state": values.tolist()}

    def restore(self, state: Dict[str, Any]) -> None:
        if state.get("engine_version") != self.mj.__version__:
            raise RunError("rigid snapshot requires the same MuJoCo version", "physics.rigid")
        kind = self.mj.mjtState.mjSTATE_INTEGRATION
        values = self.np.asarray(state["state"], dtype=float)
        if values.shape != (self.mj.mj_stateSize(self.model, kind),) or not self.np.all(self.np.isfinite(values)):
            raise RunError("invalid rigid integration state", "physics.rigid")
        self.mj.mj_setState(self.model, self.data, values, kind)
        self.mj.mj_forward(self.model, self.data)
