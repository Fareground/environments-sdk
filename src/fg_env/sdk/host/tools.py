"""Host tools: services an agent calls inside a turn (web search, retrieval, calculators).

``"mechanisms": {"search": {"kind": "host", "mode": "tool", "host": "web_search", "who": "panelist"}}``
gives panelists a ``search`` tool. Each result is recorded on the tape and kept as untrusted evidence
in the caller's own memory of the run (``$actor.search_evidence``), visible only to it, and also
published to the record ``search`` at the end of the round with ``private: false``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Untrusted
from ..registry import MechanismError, family_action, mode
from ..template import format_value
from ..world import Abort
from .common import agents_of, clip, config_of, prop_of, type_list
from .protocols import HostError
from .tape import TAPE, consult, plain, request_key, tape_prop

__all__ = ["HostToolConfig", "fetch", "prefetch"]

KEY = "host.tool"


class HostToolConfig(BaseModel):
    """A host service offered to agents as a tool."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., description="Host name of the service (a Tools adapter).")
    who: Union[str, List[str]] = Field(..., description="Agent type(s) that may call it.")
    description: str = Field("", description="Tool description the agent reads.")
    params: Dict[str, Any] = Field(default_factory=lambda: {"query": {"type": "text", "max_len": 300,
                                                                      "description": "What to look up."}},
                                   description="Tool parameters, as action params (default: one text `query`).")
    max_calls_per_turn: int = Field(3, ge=1, le=50, description="Calls per turn.")
    max_calls_per_run: Optional[int] = Field(None, ge=1, description="Calls per agent over the whole run.")
    max_chars: int = Field(4000, ge=100, le=50_000, description="Longest result kept (longer results are cut).")
    private: bool = Field(True, description="Evidence only the caller sees; false also publishes it to the record <name> at the end of the round.")
    stages: Optional[List[str]] = Field(None, description="Stages where the tool is offered (default: every stage whose actions include it).")


@mode("host", "tool", HostToolConfig,
           "A host service as an agent tool (web search, retrieval): the `<name>` tool calls the host, returns "
           "the result «quoted» and keeps it as evidence in $actor.<name>_evidence (look: <name>_evidence), "
           "within per-turn and per-run limits. Results are recorded for replay.",
           example={"host": "web_search", "who": "panelist", "max_calls_per_turn": 2, "max_calls_per_run": 6},
           was="host_tool")
def _expand_host_tool(name: str, config: HostToolConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    by = type_list(contract, config.who, "who")
    who: Union[str, List[str]] = by if len(by) > 1 else by[0]
    if name in (contract.get("actions") or {}):
        raise MechanismError(f"an action named '{name}' already exists; the tool takes the mechanism's name",
                             "rename the mechanism or the action")
    calls, evidence = f"{name}_calls", f"{name}_evidence"
    action: Dict[str, Any] = {
        "by": who,
        "description": (config.description or f"Use {name.replace('_', ' ')}.") + " The result is kept as evidence only you can see.",
        "params": config.params, "private": True, "per_turn": config.max_calls_per_turn,
        "do": [{"host": name, "action": "call", "args": "$params"}], "outcome": f"{{$last($actor.{evidence}).text}}",
    }
    when: List[Dict[str, str]] = []
    if config.max_calls_per_run is not None:
        when.append({"expr": f"$actor.{calls} < {config.max_calls_per_run}",
                     "why": f"You have used all {config.max_calls_per_run} {name} calls of this run."})
    fragment: Dict[str, Any] = {
        "types": {t: {"props": {calls: {"type": "int", "default": 0, "private": True},
                                evidence: {"type": "list", "default": [], "private": True}}} for t in by},
        "world": {"host_tape": tape_prop()},
        "actions": {name: action},
        "views": {evidence: {"for": who, "look": True, "title": f"Your {name} evidence", "of": f"$actor.{evidence}",
                             "show": "[round {$it.round}] {$it.query}: {$it.text}", "empty": "No evidence yet."}},
    }
    if config.stages:
        when.append({"expr": f"$stage in {json.dumps(config.stages)}", "why": f"{name} is not available now."})
        fragment["stage_hooks"] = {stage: {"actions": [name]} for stage in config.stages}
    if when:
        action["when"] = when
    if not config.private:
        fragment["records"] = {name: {"fields": {"query": "text", "text": "text"},
                                      "show": "{author} looked up {query}: {text}",
                                      "description": f"Evidence shared from {name}."}}
        fragment["events"] = [{"name": f"{name}_publish", "phase": "end", "do": [{"host": name, "action": "publish"}]}]
    return fragment


def fetch(world: Any, name: str, config: HostToolConfig, actor_id: str, args: Mapping[str, Any],
          lock: Any = None) -> str:
    """The host's result for this call (recorded, replayed or live)."""
    arguments = plain({key: value for key, value in args.items() if value is not None})
    result: str = consult(world, service=config.host, method="call", site=f"mechanisms.{name}", actor=actor_id,
                          identity={"args": arguments}, ask=lambda adapter: adapter.call(config.host, arguments),
                          validate=lambda answer: _result(answer, config.max_chars), lock=lock)
    return result


def prefetch(env: Any, name: str, actor: Entity, params: Mapping[str, Any]) -> Optional[str]:
    """Ask the host before the tool applies, outside the run's lock; the answer lands on the tape under it.

    Returns the tape key of an answer this call added (None when it was on the tape already, or the host was not
    asked), so a turn that ran out of time while the host answered can take it off again."""
    config = config_of(env.world, name, KEY, HostToolConfig, f"mechanisms.{name}")
    arguments = plain({key: value for key, value in params.items() if value is not None})
    with env._lock:
        calls = prop_of(actor, f"{name}_calls", 0)
        key = request_key(env.world, config.host, f"mechanisms.{name}", actor.id, {"args": arguments})
        known = key in (env.world.props.get(TAPE) or {})
    if config.max_calls_per_run is not None and calls >= config.max_calls_per_run:
        return None
    fetch(env.world, name, config, actor.id, arguments, lock=env._lock)
    return None if known else key


def _result(answer: Any, limit: int) -> str:
    if not isinstance(answer, str):
        raise HostError(f"a tool answers with text, got {type(answer).__name__}")
    return clip(answer.strip() or "(no results)", limit)


@family_action("host", ("tool",), "call", keys=("args",), required=("args",), was=("host_tool",),
               example='{"host": "search", "action": "call", "args": "$params"}  '
                       '(call the host service for the actor; the result is added to $actor.search_evidence)')
def _host_tool_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["host"]
    config = config_of(world, name, KEY, HostToolConfig, where)
    actor = vars.get("actor")
    if not isinstance(actor, Entity):
        raise RunError("`call` runs inside an action (it needs $actor)", where)
    args = runner.eval(effect["args"], vars)
    if not isinstance(args, Mapping):
        raise RunError(f"`args` must be an object, got {format_value(args)}", where)
    calls = prop_of(actor, f"{name}_calls", 0)
    if config.max_calls_per_run is not None and calls >= config.max_calls_per_run:
        raise Abort(f"You have used all {config.max_calls_per_run} {name} calls of this run.")
    arguments = plain({key: value for key, value in args.items() if value is not None})
    text = Untrusted(fetch(world, name, config, actor.id, arguments))
    query = arguments["query"] if isinstance(arguments.get("query"), str) else json.dumps(arguments, sort_keys=True)
    evidence = list(prop_of(actor, f"{name}_evidence", []))
    evidence.append({"round": world.round, "query": Untrusted(query), "args": arguments, "text": text, "shared": False})
    world.set_prop(actor, f"{name}_evidence", evidence)
    world.set_prop(actor, f"{name}_calls", calls + 1)


@family_action("host", ("tool",), "publish", internal=True, was=("publish_evidence",),
               example='{"host": "search", "action": "publish"}  (post every agent\'s unshared evidence to the record, in seat order)')
def _publish_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["host"]
    config = config_of(world, name, KEY, HostToolConfig, where)
    if config.private:
        return
    for agent in agents_of(world, config.who):
        evidence = list(prop_of(agent, f"{name}_evidence", []))
        pending = [item for item in evidence if not item.get("shared")]
        if not pending:
            continue
        for item in pending:
            world.post(name, {"query": item["query"], "text": item["text"]}, agent.id, None, where)
        world.set_prop(agent, f"{name}_evidence", [{**item, "shared": True} for item in evidence])
