"""Personas written by a host once, when the world is built.

``"mechanisms": {"lives": {"kind": "host", "mode": "personas", "who": "citizen", "prompt": "A {age}-year-old …"}}``
asks the host to write one persona per citizen from the prompt template. The text is stored in
the entity's ``persona`` property and added to its brief, so snapshots carry it and a restore
never writes it again. ``fg_env.host.load`` writes personas before round 1; with a plain
``fg_env.load`` they are written at the start of round 1, before any agent reads its brief.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contract.base import tape_prop
from ..expr import ExprError, Untrusted
from ..expr.template import compile_template, format_value
from ..host.common import MODEL_HINT, NAME, agents_of, clip, type_list
from ..host.protocols import HostError
from ..host.tape import consult, plain
from ..information.gate import render
from ..registry import MechanismError, family_action, mechanism_config, mode

__all__ = ["PersonaConfig", "generate", "KEY"]

KEY = "host.personas"


class PersonaConfig(BaseModel):
    """Personas generated per entity."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Type whose entities get a persona.")
    prompt: str = Field(..., description="What to write, as a template over $it (the entity and its props).")
    host: str = Field("personas", description="Host writer name.")
    model: str | None = Field(None, description=MODEL_HINT)
    prop: str = Field("persona", description="Text property that holds the persona.")
    brief: bool = Field(True, description="Add the persona to the entity's brief.")
    fallback: str | None = Field(None,
                                 description="Template over $it used when no host is bound (default: stop with an "
                                             "error).")
    max_chars: int = Field(2000, ge=50, le=20_000, description="Longest persona kept (longer text is cut).")


@mode("host", "personas", PersonaConfig,
           "Personas written by a host writer from a prompt template over $it, once per entity before round 1: "
           "stored in the `prop` property and the entity's brief, carried by snapshots, recorded for replay.",
           example={"who": "shopper", "prompt": "A {age}-year-old shopper with a budget of {budget|money}.",
                    "fallback": "A shopper, age {age}."})
def _expand_personas(name: str, config: PersonaConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    type_list(contract, config.who, "who")
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
        "types": {config.who: {"props": {config.prop: {"type": "text", "default": "", "private": True}}}},
        "world": {"host_tape": tape_prop()},
        "events": [{"name": f"{name}_personas", "at": 1, "phase": "start", "once": True,
                    "do": [{"host": name, "action": "write"}]}],
    }


def generate(world: Any, name: str, where: str) -> int:
    """Write every missing persona of the mechanism ``name``; returns how many were written."""
    config = mechanism_config(world, name, KEY, PersonaConfig, where)
    written = 0
    for entity in agents_of(world, config.who):
        if entity.properties.get(config.prop):
            continue
        prompt = _render(world, config.prompt, entity, f"mechanisms.{name}.prompt")
        request = plain({"task": "persona", "model": config.model, "prompt": prompt,
                         "entity": {"id": entity.id, "name": entity.name, "type": entity.entity_type}})
        fallback: Callable[[], str] | None = None
        if config.fallback is not None:
            fallback = partial(_render, world, config.fallback, entity, f"mechanisms.{name}.fallback")
        text = consult(world, service=config.host, method="write", site=f"mechanisms.{name}", actor=entity.id,
                       identity={"prompt": prompt}, ask=partial(_ask_write, request),
                       validate=lambda answer: _persona(answer, config.max_chars), fallback=fallback, moment=False)
        persona = Untrusted(clip(text.strip(), config.max_chars))
        world.set_prop(entity, config.prop, persona)
        if config.brief:
            world.add_to_brief(entity.id, f"Your persona: {format_value(persona)}")
        written += 1
    return written


def _ask_write(request: dict[str, Any], adapter: Any) -> Any:
    return adapter.write(request)


def _render(world: Any, template: str, entity: Any, path: str) -> str:
    """A persona template for a host (not an agent): the rules' own words, in the true state."""
    return render(world, template, {"it": entity}, viewer=None, subject="it", path=path).strip()


def _persona(answer: Any, limit: int) -> str:
    if not isinstance(answer, str) or not answer.strip():
        raise HostError("a persona is non-empty text")
    return clip(answer.strip(), limit)


@family_action("host", ("personas",), "write",
               example='{"host": "lives", "action": "write"}  (write any missing personas now; generated for round 1)')
def _write(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    generate(runner.world, effect["host"], where)
