"""Declarative rigid-body engine configuration."""
from typing import Any, Dict, Optional

from pydantic import Field

from .contract_base import _Model


class RigidSpec(_Model):
    model: str = Field(..., min_length=1, max_length=2_000_000, description="Self-contained MuJoCo MJCF model: bodies, joints, contacts, actuators and sensors.")
    control: Dict[str, Any] = Field(default_factory=dict, description="Actuator name to a number or SDK expression, evaluated at each rigid/equation coupling point.")
    force: Dict[str, Any] = Field(default_factory=dict, description="Body name to [fx, fy, fz, tx, ty, tz] in world coordinates (numbers or SDK expressions).")
    write: Dict[str, str] = Field(default_factory=dict, description="Observation bindings: world.<prop> or entity.<id>.<prop> to body.<name>.position, joint.<name>.position, sensor.<name>, time, contacts, or energy.")
    keyframe: Optional[str] = Field(None, description="Named MJCF keyframe for initial state; omitted uses the model's default state.")
