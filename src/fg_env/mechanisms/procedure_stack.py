"""The response stack of a procedure: items pushed and answered in response windows, resolved last in, first out.

.. code-block:: json

    "trial": {"kind": "decision", "mode": "procedure", "phases": {...},
              "stack": {"who": ["attorney", "judge"], "kinds": {
        "exhibit": {"tool": false, "params": {"name": "text"}, "responders": "$is($it, attorney) and $it.id !=
        $item.by",
                    "resolve": ["$world.admitted += $params.name"]},
        "objection": {"starts": false, "on": ["exhibit"], "who": "attorney", "responders": "$is($it, judge)",
                      "resolve": [{"if": "$world.sustained", "then": [{"decision": "trial", "action": "counter"}]}]},
        "ruling": {"starts": false, "on": ["objection"], "who": "judge", "responders": "false",
                   "params": {"sustain": "bool"}, "resolve": ["$world.sustained = $params.sustain"]}}}}

An item is pushed by its tool ``<name>_<kind>`` or by ``{"decision": name, "action": "push", "item": kind, ...}``
inside any action. While it is on top, its responders — the agents for whom the kind's
``responders`` holds — each answer it once: push an item that may sit on it (the kind lists the top's
kind in ``on``), which counts as their answer, or ``<name>_pass``. Once nobody owes an answer the top
resolves: its ``resolve`` effects run with ``$actor`` (who pushed it), ``$params``, ``$item`` and
``$below`` (the item under it, or null). The ``counter`` action removes an item without resolving it (its
``countered`` effects run instead). With ``reopen`` the item that comes back to the top gets a fresh
response window; otherwise it resolves once its earlier answers are complete.

Windows are played in the sequential stage ``<name>_stack`` (or a declared ``stage``), which wakes only
the agents who owe an answer and repeats until the stack is empty or its passes run out. Ending a
window turn without acting passes (``silence``); answers still owed when the stage ends pass too
(``unanswered``), unless either is ``wait``. State lives in the world prop ``<name>_stack``; every push,
answer, counter and resolution is a ``<name>_stack`` event, which phase transitions can wait for.

The decision family's deliberation keeps its own motion stack because motions resolve by a vote of the
body, not by effects; a stack item's ``resolve`` may still act on a deliberation (``decision`` actions).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from ..errors import RunError
from ..expr import EVERYONE, Call, ExprError, compile_expr, truthy
from ..expr.objects import Entity
from ..expr.template import format_value
from ..expr.values import _Everyone
from ..information.gate import render
from ..world.abort import Abort
from . import _common as common
from ._common import Config, Effects, stage_event
from ._social import check_expr, require_type

__all__ = ["StackConfig", "StackKind", "expand_stack", "run_step", "check_push", "check_stack_rules", "read_stack"]

READS = ("items", "top", "waiting", "can_push", "text")
_ITEM_ROOTS = ("actor", "params", "item", "below")


class StackKind(Config):
    """One kind of item that can go on the stack (a motion, an objection, a spell)."""

    title: str = Field("", description="What it is called, in plain words (default: the kind's name).")
    description: str = Field("", description="Tool description (default: generated from the rules).")
    who: str | list[str] | None = Field(None,
                                        description="Agent type(s) that may push it (default: the stack's `who`).")
    params: dict[str, Any] = Field(default_factory=dict,
                                   description="Tool params (ordinary param specs), kept on the item as $params.")
    starts: bool = Field(True, description="It may be pushed onto an empty stack.")
    on: list[str] = Field(default_factory=list, description="Kinds it may be pushed on top of (answer).")
    when: str | None = Field(None,
                             description="Extra condition to push it ($actor, $top: the item it would answer, or "
                                         "null).")
    why: str = Field("", description="What the agent is told when it may not push it.")
    responders: str = Field("$it.id != $item.by",
                            description="Who owes it an answer while it is on top: an expression over $it (an agent) "
                                        "and $item.")
    show: str = Field("", description="How the item reads after its title (template over $item, $params, $actor).")
    on_push: Effects = Field(default_factory=list,
                             description="Effects when it is pushed, e.g. paying a cost ($actor, $params, $item, "
                                         "$below).")
    resolve: Effects = Field(default_factory=list,
                             description="Effects when it resolves ($actor = who pushed it, $params, $item, $below).")
    countered: Effects = Field(default_factory=list, description="Effects when it is countered instead of resolving.")
    tool: bool = Field(True,
                       description="Generate the `<name>_<kind>` tool; false = pushed only by the push action in your "
                                   "actions.")


class StackConfig(Config):
    """A response stack: items agents answer, resolved last in, first out."""

    who: str | list[str] = Field(...,
                                 description="Agent type(s) that answer items (and push them unless a kind says "
                                             "`who`).")
    kinds: dict[str, StackKind] = Field(...,
                                        description="{kind: {title, who, params, starts, on, when, why, responders, "
                                                    "show, on_push, resolve, countered, tool}}.")
    reopen: bool = Field(True,
                         description="An item that comes back to the top (the one above resolved or was countered) "
                                     "gets a fresh response window.")
    silence: Literal["pass", "wait"] = Field("pass",
                                             description="Ending a window turn without acting: pass, or keep owing an "
                                                         "answer.")
    unanswered: Literal["pass", "wait"] = Field("pass",
                                                description="Answers still owed when the window stage ends: pass (the "
                                                            "stack resolves), or wait for the next round.")
    max_depth: int = Field(16, ge=1, le=64, description="Most items on the stack at once.")
    passes: int = Field(12, ge=1, le=100, description="Most passes of the window stage per round.")
    stage: str | None = Field(None,
                              description="Hold windows in this declared stage instead of a generated `<name>_stack` "
                                          "stage.")
    views: bool = Field(True, description="Show the agents the stack while it holds items.")

    @model_validator(mode="after")
    def _shape(self) -> StackConfig:
        if not self.kinds:
            raise ValueError("a stack needs at least one kind")
        for kind, spec in self.kinds.items():
            for below in spec.on:
                if below not in self.kinds:
                    raise ValueError(f"kind '{kind}': '{below}' in `on` is not a kind "
                                     f"({common.suggest(below, self.kinds)})")
        if not any(spec.starts for spec in self.kinds.values()):
            raise ValueError("no kind `starts` a stack, so nothing can ever be pushed")
        return self

    def player_types(self) -> list[str]:
        return [self.who] if isinstance(self.who, str) else list(self.who)

    def pushers(self, kind: str) -> list[str]:
        who = self.kinds[kind].who
        return self.player_types() if who is None else ([who] if isinstance(who, str) else list(who))

    def title(self, kind: str) -> str:
        return self.kinds[kind].title or kind.replace("_", " ")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _items(world: Any, name: str) -> list[dict[str, Any]]:
    raw = world.props.get(f"{name}_stack") or {}
    return [{**item, "responders": list(item["responders"]), "passed": list(item["passed"])}
            for item in raw.get("items", [])]


def _save(world: Any, name: str, items: list[dict[str, Any]], issued: int | None = None) -> None:
    raw = world.props.get(f"{name}_stack") or {}
    world.set_world(f"{name}_stack", {"items": items, "next": raw.get("next", 1) if issued is None else issued})


def _emit(world: Any, name: str, text: str, act: str, item: Mapping[str, Any], **extra: Any) -> None:
    world.emit(f"{name}_stack", text, data={"mechanism": name, "act": act, "item": item["id"], "kind": item["kind"]},
               **extra)


def _waiting(world: Any, item: Mapping[str, Any]) -> list[str]:
    entities = world.entities
    return [r for r in item["responders"] if r not in item["passed"] and r in entities and entities[r].alive]


def _view(world: Any, cfg: StackConfig, item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """An item as expressions read it."""
    if item is None:
        return None
    return {"id": item["id"], "kind": item["kind"], "title": cfg.title(item["kind"]), "by": item["by"],
            "params": common.thaw(item["params"], world, version=item.get("capture_version", 0)),
            "on": item["on"], "round": item["round"],
            "waiting": _waiting(world, item)}


def _vars(world: Any, cfg: StackConfig, item: Mapping[str, Any], below: Mapping[str, Any] | None) -> dict[str, Any]:
    view = _view(world, cfg, item)
    assert view is not None
    return {"actor": world.entities.get(item["by"]), "params": view["params"], "item": view,
            "below": _view(world, cfg, below)}


def _describe(world: Any, cfg: StackConfig, item: Mapping[str, Any], viewer: Entity | _Everyone | None,
              by: bool = True) -> str:
    """``item`` in words for ``viewer`` (see information/gate.py): its `show` may read only what that reader may see."""
    spec = cfg.kinds[item["kind"]]
    owner = world.entities.get(item["by"])
    text = f"{cfg.title(item['kind'])} [{item['id']}]"
    if by:
        text += f" by {owner.name if owner is not None else item['by']}"
    if spec.show:
        shown = render(world, spec.show, _vars(world, cfg, item, None), viewer=viewer)
    else:
        params = common.thaw(item["params"], world, version=item.get("capture_version", 0))
        shown = ", ".join(f"{key} {format_value(value)}" for key, value in params.items())
    return f"{text}: {shown}" if shown.strip() else text


def _names(world: Any, ids: list[str]) -> str:
    return ", ".join(world.entities[i].name for i in ids if i in world.entities) or "nobody"


# ---------------------------------------------------------------------------
# Rules and steps
# ---------------------------------------------------------------------------


def refusal(world: Any, name: str, cfg: StackConfig, kind: str, actor: Entity) -> str | None:
    """Why ``actor`` may not push ``kind`` now, or None when it may."""
    spec = cfg.kinds[kind]
    title = cfg.title(kind)
    if not any(world.is_a(actor.entity_type, t) for t in cfg.pushers(kind)):
        return f"Only {' or '.join(cfg.pushers(kind))} may push a {title}."
    items = _items(world, name)
    if len(items) >= cfg.max_depth:
        return f"The stack is full ({cfg.max_depth} items)."
    top = items[-1] if items else None
    if top is None and not spec.starts:
        return f"A {title} answers a {' or '.join(cfg.title(k) for k in spec.on)}; nothing is on the stack."
    if top is not None:
        if top["kind"] not in spec.on:
            return f"A {title} cannot answer the {cfg.title(top['kind'])} on top of the stack."
        if actor.id not in _waiting(world, top):
            return f"You do not owe the {cfg.title(top['kind'])} [{top['id']}] an answer."
    if (spec.when is not None
        and not truthy(compile_expr(spec.when)(world.evaluation.scope(actor=actor, top=_view(world, cfg, top))))):
        return spec.why or f"You cannot push a {title} now."
    return None


def _push(runner: Any, name: str, cfg: StackConfig, kind: str, actor: Entity, params: Any, where: str) -> None:
    world = runner.world
    reason = refusal(world, name, cfg, kind, actor)
    if reason is not None:
        raise Abort(reason)
    spec = cfg.kinds[kind]
    if not isinstance(params, Mapping):
        raise RunError(f"params must be a map of the kind's params, got {params!r}", f"{where}.params")
    unknown = sorted(set(params) - set(spec.params))
    if unknown:
        raise RunError(f"kind '{kind}' has no params {unknown} (params: {', '.join(spec.params) or 'none'})",
                       f"{where}.params")
    items = _items(world, name)
    issued = int((world.props.get(f"{name}_stack") or {}).get("next", 1))
    below = items[-1] if items else None
    if below is not None:
        below["passed"].append(actor.id)  # pushing an answer is this agent's answer to the item below
    item: dict[str, Any] = {"id": issued, "kind": kind, "by": actor.id, "params": common.freeze(dict(params)),
                            "capture_version": common.CAPTURE_VERSION, "on": below["id"] if below else None,
                            "round": world.round, "responders": [], "passed": []}
    view = _view(world, cfg, item)
    at = f"mechanisms.{name}.stack.kinds.{kind}"
    for player in common.carriers(world, cfg.player_types()):
        try:
            owes = truthy(compile_expr(spec.responders)(world.evaluation.scope(it=player, item=view)))
        except ExprError as exc:
            raise RunError(str(exc), f"{at}.responders") from None
        if owes:
            item["responders"].append(player.id)
    _save(world, name, items + [item], issued + 1)
    runner.run(spec.on_push, _vars(world, cfg, item, below), f"{at}.on_push")
    waiting = _waiting(world, item)
    tail = f" Waiting on {_names(world, waiting)} to answer." if waiting else ""
    _emit(world, name, f"{actor.name} pushes {_describe(world, cfg, item, EVERYONE, by=False)}.{tail}", "push", item,
          actor=actor.id)
    _settle(runner, name, cfg, where)


def _answer(runner: Any, name: str, cfg: StackConfig, actor: Entity, action: str, where: str) -> None:
    world = runner.world
    items = _items(world, name)
    top = items[-1] if items else None
    if top is None or actor.id not in _waiting(world, top):
        if action == "idle":
            return
        raise Abort("Nothing on the stack is waiting for your answer.")
    if action == "idle" and cfg.silence == "wait":
        return
    top["passed"].append(actor.id)
    _save(world, name, items)
    _emit(world, name, f"{actor.name} lets the {cfg.title(top['kind'])} [{top['id']}] stand.", "pass", top,
          actor=actor.id)
    _settle(runner, name, cfg, where)


def _settle(runner: Any, name: str, cfg: StackConfig, where: str) -> None:
    """Resolve the top while nobody owes it an answer."""
    world = runner.world
    for _ in range(4 * cfg.max_depth + 4):
        items = _items(world, name)
        if not items or _waiting(world, items[-1]):
            return
        top = items.pop()
        _save(world, name, items)
        _emit(world, name, f"The {_describe(world, cfg, top, EVERYONE)} resolves.", "resolve", top)
        runner.run(cfg.kinds[top["kind"]].resolve, _vars(world, cfg, top, items[-1] if items else None),
                   f"mechanisms.{name}.stack.kinds.{top['kind']}.resolve")
        _reopen(world, name, cfg)
    raise RunError(f"the {name} stack kept resolving items that push new ones (a loop?)", where)


def _reopen(world: Any, name: str, cfg: StackConfig) -> None:
    items = _items(world, name)
    if cfg.reopen and items and items[-1]["passed"]:
        items[-1]["passed"] = []
        _save(world, name, items)
        waiting = _waiting(world, items[-1])
        if waiting:
            back = _describe(world, cfg, items[-1], EVERYONE)
            _emit(world, name, f"Back to the {back}: waiting on {_names(world, waiting)}.", "reopen", items[-1])


def _counter(runner: Any, name: str, cfg: StackConfig, target: Any) -> None:
    world = runner.world
    items = _items(world, name)
    index = next((i for i, item in enumerate(items) if item["id"] == target), None)
    if index is None:
        return  # the item already left the stack (resolved or countered): the counter fizzles
    item = items.pop(index)
    _save(world, name, items)
    _emit(world, name, f"The {_describe(world, cfg, item, EVERYONE)} is countered.", "counter", item)
    runner.run(cfg.kinds[item["kind"]].countered, _vars(world, cfg, item, items[index - 1] if index > 0 else None),
               f"mechanisms.{name}.stack.kinds.{item['kind']}.countered")
    if index == len(items):
        _reopen(world, name, cfg)


def _close(runner: Any, name: str, cfg: StackConfig, where: str) -> None:
    world = runner.world
    if cfg.unanswered == "wait":
        # Departed responders no longer owe an answer; settle without passing for living ones.
        _settle(runner, name, cfg, where)
        return
    for _ in range(4 * cfg.max_depth + 4):
        items = _items(world, name)
        if not items:
            return
        top = items[-1]
        waiting = _waiting(world, top)
        top["passed"] += waiting
        _save(world, name, items)
        _emit(world, name,
              f"Time is up: {_names(world, waiting)} let the {cfg.title(top['kind'])} [{top['id']}] stand.",
              "timeout", top)
        _settle(runner, name, cfg, where)
    raise RunError(f"the {name} stack kept growing while it was closed (a loop?)", where)


def run_step(runner: Any, name: str, cfg: StackConfig, action: str, effect: Mapping[str, Any], vars: dict[str, Any],
             where: str) -> None:
    """One stack action of the decision op: push, pass, idle, counter or close."""
    world = runner.world
    if action == "close":
        _close(runner, name, cfg, where)
        return
    if action == "counter":
        if "target" in effect:
            target = runner.eval(effect["target"], vars)
        elif isinstance(vars.get("item"), Mapping):  # inside a kind's effects: the item under that one
            target = (vars.get("below") or {}).get("id")
        else:
            items = _items(world, name)
            target = items[-1]["id"] if items else None
        _counter(runner, name, cfg, target)
        _settle(runner, name, cfg, where)
        return
    actor = world.entity(runner.eval(effect["who"], vars) if "who" in effect else vars.get("actor"))
    if actor is None:
        raise RunError(f"`{action}` needs an agent: run it inside an action, or give `who`", where)
    if action == "push":
        kind = effect["item"]
        if kind not in cfg.kinds:
            raise RunError(f"'{kind}' is not a kind of the {name} stack ({common.suggest(str(kind), cfg.kinds)})",
                           f"{where}.item")
        _push(runner, name, cfg, kind, actor, runner.eval(effect.get("params") or {}, vars), where)
    else:
        _answer(runner, name, cfg, actor, action, where)


# ---------------------------------------------------------------------------
# Checks, $stack and expansion
# ---------------------------------------------------------------------------


def check_push(name: str, cfg: StackConfig, effect: Mapping[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    """Problems with a push action: the item must be a kind of the stack."""
    item = effect.get("item")
    if item in cfg.kinds:
        return []
    return [(f"{path}.item", f"'{item}' is not a kind of the {name} stack", common.suggest(str(item), cfg.kinds))]


def check_stack_rules(checker: Any, name: str, cfg: StackConfig) -> None:
    """Check the kinds' effects and templates."""
    roots = set(common.base_roots()) | set(_ITEM_ROOTS)
    for kind, spec in cfg.kinds.items():
        at = f"mechanisms.{name}.stack.kinds.{kind}"
        for key in ("on_push", "resolve", "countered"):
            checker.effects(getattr(spec, key), f"{at}.{key}", roots, {})
        checker.template(spec.show or None, f"{at}.show", None, roots)
        checker._shared_text(spec.show or None, f"{at}.show", {"actor": set(cfg.pushers(kind))})  # in the news


def _entity_arg(call: Call, index: int) -> Entity:
    found = call.scope.world.entity(call.arg(index))
    if found is None:
        raise ExprError(f"$stack: argument {index + 1} must be an entity or id, got {call.arg(index)!r}", call.source)
    return found  # type: ignore[no-any-return]


def read_stack(call: Call, name: str, cfg: StackConfig) -> Any:
    """``$stack(procedure, read, ...)``."""
    world: Any = call.scope.world
    read = call.arg(1) if len(call) > 1 else "items"
    arity = {"items": (1, 2), "top": (2, 2), "waiting": (2, 3), "can_push": (4, 4), "text": (2, 3)}.get(read)
    if arity is None:
        raise ExprError(f"$stack: read must be one of {', '.join(READS)}, got {read!r}", call.source)
    if not arity[0] <= len(call) <= arity[1]:
        extra = {"waiting": " (and optionally an agent)", "can_push": " a kind and an agent",
                 "text": " (and optionally a viewer)"}
        raise ExprError(f"$stack: `{read}` takes the procedure name{extra.get(read, '')}", call.source)
    items = _items(world, name)
    if read == "items":
        return [_view(world, cfg, item) for item in items]
    if read == "top":
        return _view(world, cfg, items[-1]) if items else None
    if read == "waiting":
        waiting = _waiting(world, items[-1]) if items else []
        return _entity_arg(call, 2).id in waiting if len(call) > 2 else waiting
    if read == "can_push":
        kind = call.arg(2)
        if kind not in cfg.kinds:
            raise ExprError(f"$stack: '{kind}' is not a kind of the {name} stack "
                            f"({common.suggest(str(kind), cfg.kinds)})", call.source)
        return refusal(world, name, cfg, kind, _entity_arg(call, 3)) is None
    return _text(world, name, cfg, _entity_arg(call, 2) if len(call) > 2 else None, call.scope.vars.get("viewer"))


def _text(world: Any, name: str, cfg: StackConfig, answering: Entity | None,
          reader: Entity | _Everyone | None) -> str:
    """The stack in words for ``reader`` (whoever reads the text that asks for it), with the answers ``answering``
    may give when it owes one."""
    items = _items(world, name)
    if not items:
        return "The stack is empty."
    lines = [f"{'Top' if depth == 0 else 'Below'}: {_describe(world, cfg, item, reader)}"
             + (f" (answers [{item['on']}])" if item["on"] is not None else "")
             for depth, item in enumerate(reversed(items))]
    waiting = _waiting(world, items[-1])
    lines.append(f"Waiting on: {_names(world, waiting)}.")
    if answering is not None and answering.id in waiting:
        options = [cfg.title(k) for k, spec in cfg.kinds.items() if spec.tool
                   and refusal(world, name, cfg, k, answering) is None]
        lines.append("You may answer with " + (", ".join(options) + " or pass." if options else "a pass."))
    return "\n".join(lines)


def expand_stack(name: str, cfg: StackConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    """The sections a procedure's stack adds: its state, tools, window stage (or hook) and view."""
    for type_name in cfg.player_types():
        require_type(contract, type_name, "stack.who", agent=True)
    for kind, spec in cfg.kinds.items():
        at = f"stack.kinds.{kind}"
        if not common.NAME.match(kind):
            raise common.MechanismError(f"kind name '{kind}' must start with a letter and use letters, digits and _",
                                        None, at)
        for type_name in cfg.pushers(kind) if spec.who is not None else ():
            require_type(contract, type_name, f"{at}.who", agent=True)
        check_expr(spec.when, f"{at}.when", ("actor", "top"))
        check_expr(spec.responders, f"{at}.responders", ("it", "item"))
    op = {"decision": name}
    actions: dict[str, Any] = {}
    for kind, spec in cfg.kinds.items():
        if not spec.tool:
            continue
        title = cfg.title(kind)
        answers = f" It answers a {' or '.join(cfg.title(k) for k in spec.on)} on top of the stack." if spec.on else ""
        actions[f"{name}_{kind}"] = {
            "by": cfg.pushers(kind),
            "description": spec.description or f"Push a {title} onto the stack.{answers}"
            + ("" if spec.starts else " It cannot start a stack."), "params": spec.params,
            "when": [{"expr": f"$stack({name}, can_push, {kind}, $actor)",
                      "why": spec.why or f"You cannot push a {title} now."}],
            "do": [{**op, "action": "push", "item": kind, "params": {p: f"$params.{p}" for p in spec.params}}],
            "outcome": f"You pushed a {title}.", "private": True, "terminal": True,
        }
    players = cfg.player_types()
    actions[f"{name}_pass"] = {
        "by": players, "description": "Let the item on top of the stack stand without answering it.",
        "when": [{"expr": f"$stack({name}, waiting, $actor)",
                  "why": "Nothing on the stack is waiting for your answer."}],
        "do": [{**op, "action": "pass"}], "outcome": "You let it stand.", "private": True, "terminal": True,
    }
    stage = cfg.stage or f"{name}_stack"
    events = [stage_event(stage, "end", [{**op, "action": "close"}])]
    if cfg.silence == "pass":
        events.append(stage_event(stage, "turn", [{**op, "action": "idle"}], when="not $acted"))
    fragment: dict[str, Any] = {
        "world": {f"{name}_stack": {"type": "map", "default": {"items": [], "next": 1},
                                    "description": "The stack: its items, bottom first."}},
        "actions": actions, "events": events,
    }
    if cfg.stage is None:
        fragment["stages"] = [{"name": stage, "turns": "sequential", "actions": list(actions),
                               "who": f"$stack({name}, waiting, $it)", "until": f"$stack({name}, top) == null",
                               "when": f"$stack({name}, top) != null", "passes": cfg.passes,
                               "brief": "Answer the item on top of the stack, or let it stand."}]
    else:
        fragment["stage_hooks"] = {cfg.stage: {"actions": list(actions)}}
    if cfg.views:
        fragment["views"] = {f"{name}_stack": {"for": players, "title": "The stack",
                                               "when": f"$stack({name}, top) != null",
                                               "show": f"{{$stack({name}, text, $actor)}}", "bullet": False}}
    return fragment
