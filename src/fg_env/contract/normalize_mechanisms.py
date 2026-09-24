"""Normalization rules for mechanisms and the function library: renamed families, removed modes (refused with what to
write instead), and the sections that became mechanisms."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from ..errors import ContractError, Issue
from .normalize import rule

#: ``(old family, mode)`` → the family the mode moved to (the mode keeps its name).
MOVED_MODES = {("flow", "procedure"): "decision", ("operations", "queue"): "economy", ("conditions", "status"): "game",
               ("mind", "memory"): "host", ("mind", "personas"): "host"}
#: Families that no longer exist: an effect op named after one is its renamed family's op.
_OLD_OPS = {old: new for (old, _), new in MOVED_MODES.items()}

#: ``family.mode`` removed, and how to say the same without it.
REMOVED_MODES = {
    "agreements.labor":
        "declare jobs yourself: a `job` type (wage, employer, worker), `post` and `hire` actions, and a ledger's `pay` "
        "for wages each round in an event",
    "flow.order": "set the stage's `order` (\"order\": \"-$it.speed\" acts fastest first) and skip agents with its "
                  "`who`",
    "flow.victory":
        "declare `end` entries with a `winner` ({\"when\": \"$count(hero, $it.hp > 0) <= 1\", \"winner\": "
        "\"$best($filter(hero, $it.hp > 0), $it.hp)\"}) and score the players with `types.<type>.score`",
    "game.slots": "declare each space as an entity with a `capacity`, and a `place` action whose param's `where` keeps "
                  "only spaces with room",
    "conditions.cooldowns":
        "use a `game.status` mechanism: one status per cooldown whose `blocks` lists the action, applied in that "
        "action's `do` ({\"game\": <name>, \"action\": \"apply\", \"status\": ..., \"who\": \"$actor\"})",
    "conditions.channeling": "keep the channel in a property (rounds left) and resolve it in an event at the start of "
                             "the round",
    "conditions.terrain": "declare the terrain as a space layer (`space.layers`) and read it with $layer",
    "groups.relationships": "declare the relation in `relations` (a value per pair, with min and max) and drift it in "
                            "an event",
    "groups.factions": "declare membership as a relation or a prop, with actions to join and leave",
    "social.channels":
        "post to a record ({\"post\": \"chat\", \"to\": ...}) whose `visible` says who reads each entry",
    "mind.beliefs": "keep each agent's beliefs in props with `private: true`",
}


@rule
def moved_modes(data: dict[str, Any]) -> list[str]:
    """Mechanisms of a family that was folded into another (``flow.procedure`` → ``decision.procedure``), and the
    effect ops named after the old family."""
    notes = []
    for name, use in _uses(data):
        new = MOVED_MODES.get((use.get("kind"), use.get("mode")))  # type: ignore[arg-type]
        if new is not None:
            notes.append(f"mechanisms.{name}: kind '{use['kind']}' → '{new}' (mode '{use['mode']}')")
            use["kind"] = new
    for path, effect in _dicts(data, ""):
        old = next((key for key in _OLD_OPS if isinstance(effect.get(key), str)), None)
        if old is None or not isinstance(effect.get("action"), str) or _OLD_OPS[old] in effect:
            continue
        renamed = {(_OLD_OPS[old] if key == old else key): value for key, value in effect.items()}
        effect.clear()
        effect.update(renamed)
        notes.append(f"{path}: effect op `{old}` → `{_OLD_OPS[old]}`")
    return notes


@rule
def removed_modes(data: dict[str, Any]) -> list[str]:
    """Mechanism modes that were removed: refused, each with the ordinary contract parts that say the same."""
    issues = [Issue(f"mechanisms.{name}", f"the `{key}` mechanism was removed", REMOVED_MODES[key])
              for name, use in _uses(data) if (key := f"{use.get('kind')}.{use.get('mode')}") in REMOVED_MODES]
    if issues:
        raise ContractError(issues)
    return []


def _uses(data: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    uses = data.get("mechanisms")
    if not isinstance(uses, Mapping):
        return []
    return [(str(name), use) for name, use in uses.items() if isinstance(use, dict)]


def _dicts(value: Any, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every object inside ``value`` (itself included), with its path."""
    if isinstance(value, dict):
        yield path, value
        for key, item in list(value.items()):
            yield from _dicts(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _dicts(item, f"{path}[{index}]")


# -- functions removed from the library ------------------------------------------------------------------------------

#: Removed function → how its call is written now, from the call's arguments (None: it takes other arguments).
_REWRITES: dict[str, Callable[[list[str]], str | None]] = {
    "random": lambda args: "$uniform(0, 1)" if not args else None,
    "exists": lambda args: f"$get($entity({args[0]}), 'alive', false)" if len(args) == 1 else None,
    "ids": lambda args: f"$map({args[0]}, $it.id)" if len(args) == 1 else None,
    "index_of": lambda args: f"$index({args[0]}, {args[1]})" if len(args) == 2 else None,
    "count_text": lambda args: f"($len($split({args[0]}, {args[1]})) - 1)" if len(args) == 2 else None,
}
_CALL = re.compile(r"(?<![A-Za-z0-9_$.])\$(" + "|".join(_REWRITES) + r")\(")
_OPEN, _CLOSE = "([{", ")]}"


@rule
def removed_functions(data: dict[str, Any]) -> list[str]:
    """Calls of functions that duplicated another (``$random()`` is ``$uniform(0, 1)``), in every expression and
    template; a contract's own def of the same name is left alone."""
    defs = data.get("defs")
    own = set(defs) if isinstance(defs, Mapping) else set()
    notes: list[str] = []

    def visit(value: Any, path: str) -> Any:
        if isinstance(value, str):
            rewritten = _rewrite_calls(value, own)
            if rewritten != value:
                notes.append(f"{path}: `{value}` → `{rewritten}`")
            return rewritten
        if isinstance(value, dict):
            for key, item in value.items():
                value[key] = visit(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = visit(item, f"{path}[{index}]")
        return value

    visit(data, "")
    return notes


def _rewrite_calls(text: str, own: set[str]) -> str:
    out, at = [], 0
    for match in _CALL.finditer(text):
        if match.start() < at or match.group(1) in own:
            continue
        parsed = _arguments(text, match.end())
        if parsed is None:  # unbalanced: the checker reports it as written
            continue
        args, end = parsed
        replacement = _REWRITES[match.group(1)]([_rewrite_calls(arg, own) for arg in args])
        if replacement is None:
            continue
        out += [text[at:match.start()], replacement]
        at = end
    return "".join(out) + text[at:] if out else text


def _arguments(text: str, start: int) -> tuple[list[str], int] | None:
    """The arguments of the call whose ``(`` ends at ``start`` (stripped), and where the call ends."""
    depth, quote, args, begin, index = 0, "", [], start, start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in _OPEN:
            depth += 1
        elif char in _CLOSE and depth:
            depth -= 1
        elif char == ")":
            args.append(text[begin:index].strip())
            return ([] if args == [""] else args), index + 1
        elif char == "," and not depth:
            args.append(text[begin:index].strip())
            begin = index + 1
        index += 1
    return None
