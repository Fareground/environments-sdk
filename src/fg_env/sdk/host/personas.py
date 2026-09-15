"""Personas written by a host once, when the world is built.

``"mechanisms": {"lives": {"kind": "personas", "of": "citizen", "prompt": "A {age}-year-old …"}}``
asks the host to write one persona per citizen from the prompt template. The text is stored in
the entity's ``persona`` property and added to its brief, so snapshots carry it and a restore
never writes it again. ``fg_env.sdk.host.load`` writes personas before round 1; with a plain
``fg_env.load`` they are written at the start of round 1, before any agent reads its brief.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Callable, Dict, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import ExprError, Untrusted
from ..registry import MechanismError, effect_op, mechanism
from ..template import compile_template, format_value
from .common import NAME, agents_of, clip, config_of, declared_check, type_list
from .protocols import HostError
from .tape import consult, plain, tape_prop

__all__ = ["PersonaConfig", "generate"]


class PersonaConfig(BaseModel):
    """Personas generated per entity."""

    model_config = ConfigDict(extra="forbid")

    of: str = Field(..., description="Type whose entities get a persona.")
    prompt: str = Field(..., description="What to write, as a template over $it (the entity and its props).")
    host: str = Field("personas", description="Host writer name.")
    model: Optional[str] = Field(None, description="Model hint passed to the host.")
    prop: str = Field("persona", description="Text property that holds the persona.")
    brief: bool = Field(True, description="Add the persona to the entity's brief.")
    fallback: Optional[str] = Field(None, description="Template over $it used when no host is bound (default: stop with an error).")
    max_chars: int = Field(2000, ge=50, le=20_000, description="Longest persona kept (longer text is cut).")


@mechanism("personas", PersonaConfig,
           "Personas written by a host writer from a prompt template over $it, once per entity before round 1: "
           "stored in the `prop` property and the entity's brief, carried by snapshots, recorded for replay.",
           example={"kind": "personas", "of": "shopper", "prompt": "A {age}-year-old shopper with a budget of {budget|money}.",
                    "fallback": "A shopper, age {age}."})
def _expand_personas(name: str, config: PersonaConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    type_list(contract, config.of, "of")
    if not NAME.match(config.prop):
        raise MechanismError(f"prop must be a property name, got {config.prop!r}", None, "prop")
    for key, template in (("prompt", config.prompt), ("fallback", config.fallback)):
        if template is None:
            continue
        try:
            compile_template(template, "it")
        except ExprError as exc:
            raise MechanismError(str(exc), "fix the template", key) from None
    return {
        "types": {config.of: {"props": {config.prop: {"type": "text", "default": "", "private": True}}}},
        "world": {"host_tape": tape_prop()},
        "events": [{"name": f"{name}_personas", "at": 1, "phase": "start", "once": True, "do": [{"personas": name}]}],
    }


def generate(world: Any, name: str, where: str) -> int:
    """Write every missing persona of the mechanism ``name``; returns how many were written."""
    config = config_of(world, name, "personas", PersonaConfig, where)
    written = 0
    for entity in agents_of(world, config.of):
        if entity.properties.get(config.prop):
            continue
        prompt = _render(world, config.prompt, entity, f"mechanisms.{name}.prompt")
        request = plain({"task": "persona", "model": config.model, "prompt": prompt,
                         "entity": {"id": entity.id, "name": entity.name, "type": entity.entity_type}})
        fallback: Optional[Callable[[], str]] = None
        if config.fallback is not None:
            fallback = partial(_render, world, config.fallback, entity, f"mechanisms.{name}.fallback")
        text = consult(world, service=config.host, method="write", site=f"mechanisms.{name}", actor=entity.id,
                       identity={"prompt": prompt}, ask=partial(_ask_write, request),
                       validate=lambda answer: _persona(answer, config.max_chars), fallback=fallback, moment=False)
        persona = Untrusted(clip(text.strip(), config.max_chars))
        world.set_prop(entity, config.prop, persona)
        if config.brief:
            _add_to_brief(world, entity.id, f"Your persona: {format_value(persona)}")
        written += 1
    return written


def _ask_write(request: Dict[str, Any], adapter: Any) -> Any:
    return adapter.write(request)


def _render(world: Any, template: str, entity: Any, path: str) -> str:
    try:
        return compile_template(template, "it").render(world.scope(it=entity)).strip()
    except ExprError as exc:
        raise RunError(str(exc), path) from None


def _persona(answer: Any, limit: int) -> str:
    if not isinstance(answer, str) or not answer.strip():
        raise HostError("a persona is non-empty text")
    return clip(answer.strip(), limit)


def _add_to_brief(world: Any, entity_id: str, line: str) -> None:
    had = entity_id in world.entity_briefs
    old = world.entity_briefs.get(entity_id)
    world.entity_briefs[entity_id] = f"{old}\n{line}" if old else line

    def undo() -> None:
        if had:
            world.entity_briefs[entity_id] = old
        else:
            world.entity_briefs.pop(entity_id, None)

    world.journal.push(undo)


@effect_op("personas", keys=(), literal=("personas",),
           example='{"personas": "lives"}  (write any missing personas of a declared personas mechanism now)',
           check=declared_check("personas", "personas"))
def _personas_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    generate(runner.world, effect["personas"], where)
