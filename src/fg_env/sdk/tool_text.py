"""Tool descriptions a model can plan with: limits in words, usage caps, and actions that share one tool.

A model counts words, not characters, so a text limit is stated as both (``Up to 400 characters (about 60 words).``);
a per-turn or per-round cap is stated before the model spends a call learning it. Actions offered inside one shared
tool (a mechanism's ``tools: one``) are each described on their own line with the arguments they take, and an argument
several of them take keeps every constraint: one merged schema of their common type (enums joined, bounds widened),
with each action's own range or choices in its description.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["text_limit", "usage_limits", "shared_description", "shared_param"]

#: Characters in an average English word with the space after it: turns a character limit into words.
CHARS_PER_WORD = 6.5
#: Schema keys that constrain a value, merged across the actions sharing an argument.
_BOUNDS = ("minimum", "maximum", "maxLength", "minItems", "maxItems")


def text_limit(max_len: int) -> str:
    """``Up to 400 characters (about 60 words).`` — the word count rounded down to its leading digit."""
    words = int(max_len / CHARS_PER_WORD)
    step = 10 ** max(0, len(str(words)) - 1)
    rounded = max(1, words // step * step)
    return f"Up to {max_len} characters (about {rounded} word{'s' if rounded != 1 else ''})."


def usage_limits(per_turn: Optional[int], per_round: Optional[int]) -> str:
    """``Once per turn.`` / ``At most 3 times per round.`` — empty when the action has no cap."""
    parts = [f"{_times(count)} per {period}" for count, period in ((per_turn, "turn"), (per_round, "round"))
             if count is not None]
    if not parts:
        return ""
    text = " and ".join(parts)
    return text[0].upper() + text[1:] + "."


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"at most {count} times")


def shared_description(group: str, members: Sequence[Tuple[str, str, Sequence[str]]], staged: bool) -> str:
    """The description of a shared tool: ``members`` are (choice name, description, arguments) of the legal actions."""
    lines = [f"{group.replace('_', ' ').capitalize()}: choose one `action` and pass only the arguments it takes. "
             "Only these actions are available now:"]
    for choice, description, arguments in members:
        takes = f" Arguments: {', '.join(arguments)}." if arguments else " No arguments."
        lines.append(f"- {choice}: {description}{takes}")
    if staged:
        lines.append("(Committed when everyone has chosen.)")
    return "\n".join(lines)


def shared_param(entries: Sequence[Tuple[str, Dict[str, Any]]], total: int) -> Dict[str, Any]:
    """One property of a shared tool from the schemas of the actions taking it (``entries``: choice name, schema)."""
    shapes: List[Dict[str, Any]] = []
    for _, schema in entries:
        bare = {key: value for key, value in schema.items() if key != "description"}
        if bare not in shapes:
            shapes.append(bare)
    merged = dict(shapes[0]) if len(shapes) == 1 else _merged(shapes)
    out: Dict[str, Any] = merged if merged is not None else {"anyOf": shapes}
    described: Dict[str, List[str]] = {}
    for who, schema in entries:
        own = _narrower(schema, out) if len(shapes) > 1 and merged is not None else ""
        text = " ".join(part for part in (schema.get("description", ""), own) if part)
        described.setdefault(text, []).append(who)
    texts = [text for text in described if text]
    if len(described) == 1:
        about = texts[0] if texts else ""
    else:
        about = " ".join(f"For {', '.join(who)}: {text.rstrip('.')}." for text, who in described.items() if text)
    users = "" if len(entries) == total else f"Only for {', '.join(who for who, _ in entries)}."
    description = " ".join(part for part in (users, about) if part)
    if description:
        out["description"] = description
    return out


def _merged(shapes: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One schema accepting what each of ``shapes`` accepts, when they share a type; None when they do not."""
    kinds = {shape.get("type") for shape in shapes}
    if len(kinds) != 1 or None in kinds:
        return None
    out: Dict[str, Any] = {"type": kinds.pop()}
    if all("enum" in shape for shape in shapes):
        joined: List[Any] = []
        for shape in shapes:
            joined.extend(value for value in shape["enum"] if value not in joined)
        out["enum"] = joined
    for key in _BOUNDS:
        values = [shape.get(key) for shape in shapes]
        if all(value is not None for value in values):
            out[key] = min(values) if key in ("minimum", "minItems") else max(values)
    for key in ("multipleOf", "items", "uniqueItems", "default"):
        values = [shape.get(key) for shape in shapes]
        if values[0] is not None and all(value == values[0] for value in values):
            out[key] = values[0]
    return out


def _narrower(schema: Dict[str, Any], merged: Dict[str, Any]) -> str:
    """How ``schema`` constrains its value more than the merged schema does, in words."""
    parts = []
    enum = schema.get("enum")
    if enum is not None and enum != merged.get("enum"):
        parts.append("One of: " + ", ".join(str(value) for value in enum) + ".")
    low, high = schema.get("minimum"), schema.get("maximum")
    if (low, high) != (merged.get("minimum"), merged.get("maximum")) and (low is not None or high is not None):
        parts.append(f"From {low} to {high}." if low is not None and high is not None else
                     (f"At least {low}." if low is not None else f"At most {high}."))
    if schema.get("maxLength") is not None and schema.get("maxLength") != merged.get("maxLength"):
        parts.append(text_limit(schema["maxLength"]))
    return " ".join(parts)
