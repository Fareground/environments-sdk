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


@rule
def sections_as_mechanisms(data: dict[str, Any]) -> list[str]:
    """The ``physics``, ``feeds`` and ``patterns`` sections, which are mechanisms now: ``physics`` the one named
    physics (kind ``dynamics``, mode ``ode``), each feed a ``host.feed``, each pattern a ``pattern`` whose mode is its
    kind (a triangular draw's ``mode`` is its ``peak``) — in the contract and in its arms' patches. A pattern named
    like another mechanism is renamed ``<name>_pattern``, with every reference to it."""
    arms = data.get("arms")
    patches = [(f"arms.{arm}.patch", spec["patch"]) for arm, spec in (arms.items() if isinstance(arms, Mapping) else ())
               if isinstance(spec, Mapping) and isinstance(spec.get("patch"), dict)]
    moving = ("physics", "feeds", "patterns")
    if not any(key in part for _, part in [("", data), *patches] for key in moving):
        return []
    uses = data.setdefault("mechanisms", {}) if any(key in data for key in moving) else data.get("mechanisms", {})
    if not isinstance(uses, dict):
        return []  # the parser reports the malformed section
    section = data.get("patterns")
    patterns: Mapping[str, Any] = section if isinstance(section, Mapping) else {}
    kinds = {name: spec.get("kind") for name, spec in patterns.items() if isinstance(spec, Mapping)}
    renames, notes = _renamed_patterns(data, uses, patterns)
    kinds = {renames.get(name, name): kind for name, kind in kinds.items()}
    notes += _moved(data, uses, "", renames, kinds, whole=True)
    for path, patch in patches:
        notes += _moved(patch, patch.setdefault("mechanisms", {}), f"{path}.", renames, kinds, whole=False)
    return notes


def _renamed_patterns(data: dict[str, Any], uses: Mapping[str, Any], patterns: Mapping[str, Any]
                      ) -> tuple[dict[str, str], list[str]]:
    """The patterns named like a mechanism of another kind, renamed (old → new) with every reference, and a note for
    each."""
    taken, renames, notes = {*uses, *patterns}, {}, []
    for name in [name for name in patterns if name in uses and not _is(uses[name], "pattern")]:
        target = _free(f"{name}_pattern", taken)
        taken.add(target)
        renames[name] = target
        _rename_pattern(data, name, target)
        notes.append(f"patterns.{name} is named like mechanism '{name}': renamed '{target}' with every reference "
                     "(its random draws follow the new name)")
    return renames, notes


def _moved(part: dict[str, Any], uses: Any, path: str, renames: Mapping[str, str], kinds: Mapping[str, Any],
           whole: bool) -> list[str]:
    """Move one document's (or patch's) sections into its ``mechanisms``. An entry updates the mechanism of its name
    when there is one of its kind (a document mixing both forms); in a patch, an entry that names no kind changes the
    contract's mechanism of that name, so it gets no kind and mode of its own."""
    if not isinstance(uses, dict):
        return []
    notes = []
    physics = part.get("physics")
    if isinstance(physics, Mapping):
        existing = uses.get("physics")
        if existing is not None and not _is(existing, "dynamics", "ode"):
            raise ContractError([Issue(f"{path}mechanisms.physics", "the `physics` section is now the mechanism named "
                                       "physics, and another mechanism has that name", "rename that mechanism")])
        del part["physics"]
        uses["physics"] = {"kind": "dynamics", "mode": "ode", **(existing or {}), **physics}
        notes.append(f"{path}physics → {path}mechanisms.physics (kind 'dynamics', mode 'ode')")
    for name, spec in _section(part, "feeds"):
        existing = uses.get(name)
        if existing is not None and not _is(existing, "host", "feed"):
            raise ContractError([Issue(f"{path}feeds.{name}", "feeds are mechanisms now, and another mechanism is "
                                       f"named '{name}'", "rename the feed")])
        uses[name] = {"kind": "host", "mode": "feed", **(existing or {}), **spec}
        notes.append(f"{path}feeds.{name} → {path}mechanisms.{name} (kind 'host', mode 'feed')")
    for old, spec in _section(part, "patterns"):
        name = renames.get(old, old)
        kind = spec.pop("kind", None)
        existing = uses.get(name) if _is(uses.get(name), "pattern") else None
        if (kind or (existing or {}).get("mode") or kinds.get(name)) == "draw" and "mode" in spec:
            spec["peak"] = spec.pop("mode")
        entry = {**(existing or {}), **spec}
        if kind is not None or (whole and not existing):
            entry = {"kind": "pattern", "mode": kind, **{k: v for k, v in entry.items() if k not in ("kind", "mode")}}
        uses[name] = entry
        notes.append(f"{path}patterns.{old} → {path}mechanisms.{name} (kind 'pattern', mode '{entry.get('mode')}')")
    return notes


def _is(use: Any, kind: str, mode: str | None = None) -> bool:
    return isinstance(use, Mapping) and use.get("kind") == kind and (mode is None or use.get("mode") == mode)


def _section(data: dict[str, Any], key: str) -> list[tuple[str, dict[str, Any]]]:
    """The entries of a section moved into mechanisms (the section itself removed); a malformed one stays for the
    parser to report."""
    section = data.get(key)
    if not isinstance(section, Mapping) or not all(isinstance(spec, Mapping) for spec in section.values()):
        return []
    del data[key]
    return [(str(name), dict(spec)) for name, spec in section.items()]


def _free(name: str, taken: set[str]) -> str:
    candidate, number = name, 1
    while candidate in taken:
        number += 1
        candidate = f"{name}_{number}"
    return candidate


#: Config fields that name a pattern (a demand's rate, factors and noise; a product's operands; a lead time …).
_PATTERN_FIELDS = frozenset({"pattern", "noise", "rate", "factors", "of", "adjust"})


def _rename_pattern(data: Any, old: str, new: str) -> None:
    """Point every reference to the pattern ``old`` at ``new``: reads in expressions and templates, and the config
    fields that name a pattern."""
    reads = re.compile(r"(\$pattern\.)" + re.escape(old) + r"(?![A-Za-z0-9_])|(\$pattern_values\(\s*['\"])"
                       + re.escape(old) + r"(?=['\"])")

    def visit(value: Any, key: str | None) -> Any:
        if isinstance(value, str):
            if key in _PATTERN_FIELDS and value == old:
                return new
            return reads.sub(lambda m: (m.group(1) or m.group(2)) + new, value)
        if isinstance(value, list):
            return [visit(item, key if key in _PATTERN_FIELDS else None) for item in value]
        if isinstance(value, dict):
            renamed = {(new if key == "x" and name == old else name): visit(item, name)
                       for name, item in value.items()}  # a fit's x: {pattern: column}
            value.clear()
            value.update(renamed)
        return value

    visit(data, None)


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
