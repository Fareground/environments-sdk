"""Participants that behave badly on purpose, as models and scripts do: none of them may crash a run.

Each is deterministic (its choices follow from the turn it is in) and stops when the turn is over.
"""
import random
from typing import Any, Dict

from fg_env.participants import sample_args

HUGE = "x" * 100_000


def _rng(wake: Any) -> random.Random:
    return random.Random(f"{wake.entity_id}/{wake.round}/{wake.stage}")


def _acts(wake: Any) -> list:
    return [tool for tool in wake.tools if tool.kind == "act"]


def refuser(wake: Any) -> None:
    """A model that refuses every turn: it replies without calling a tool."""
    wake.record_usage(llm_calls=1, refusals=1)


def spammer(wake: Any) -> None:
    """A model that calls tools that do not exist and real tools with malformed arguments until the turn is over."""
    wake.record_usage(llm_calls=1)
    names = [tool.name for tool in _acts(wake)] or ["end_turn"]
    junk = [("no_such_tool", {}), ("", None), (names[0], {"nope": 1}), (names[0], ["not", "an", "object"]),
            (names[0], {name: {"deep": [None]} for name in ("p0", "p1", "id", "view")}), ("LOOK", {"view": 1})]
    for name, args in junk * 3:
        if wake.done:
            return
        wake.call(name, args)


def _edge(prop: Dict[str, Any], high: bool) -> Any:
    if "enum" in prop:
        return prop["enum"][-1 if high else 0] if prop["enum"] else ""
    kind = prop.get("type")
    if kind in ("integer", "number"):
        return prop.get("maximum" if high else "minimum", 10**12 if high else -10**12)
    if kind == "array":
        return []
    if kind == "boolean":
        return high
    return HUGE if high else ""


def boundary(wake: Any) -> None:
    """Calls every action with every argument at one edge of what its schema allows, then at the other: the minimum
    and the maximum, the first and the last choice, an empty list, an empty and a huge text."""
    for high in (wake.round % 2 == 0, wake.round % 2 == 1):
        for tool in _acts(wake):
            if wake.done:
                return
            props = tool.input_schema.get("properties", {})
            wake.call(tool.name, {name: _edge(prop, high) for name, prop in props.items()})


def prober(wake: Any) -> None:
    """Retries refused actions — the same call again, then fresh arguments — until one lands or the calls run out."""
    rng = _rng(wake)
    while not wake.done and wake.calls_left > 0:
        acts = _acts(wake)
        if not acts:
            return
        tool = rng.choice(acts)
        args = sample_args(tool.input_schema, rng)
        if wake.call(tool.name, args).ok or wake.done:
            return
        wake.call(tool.name, args)


def _outside(prop: Dict[str, Any]) -> Any:
    """A value the parameter's schema rules out."""
    if "enum" in prop:
        return "no such choice"
    if prop.get("type") == "boolean":
        return "maybe"
    if "maximum" in prop:
        return prop["maximum"] + 1000
    return {"not": "a value"}


def probing(agent: Any) -> Any:
    """``agent``, after first making one call its tool's schema rules out: a choice not offered, a number past the
    maximum, an object where a value belongs, an argument that does not exist."""

    def play(wake: Any) -> None:
        acts = _acts(wake)
        if acts and wake.calls_left >= 4:  # the probe, the move and ending the turn leave a call unused
            props = acts[0].input_schema.get("properties", {})
            bad = {name: _outside(prop) for name, prop in props.items()} or {"no_such_argument": 1}
            assert not wake.call(acts[0].name, bad).ok
        agent(wake)
    return play


def played(result: Any) -> tuple:
    """What a run did, leaving out the refused calls themselves."""
    kept = [{k: e.get(k) for k in ("round", "kind", "actor", "text", "data")} for e in result.events if e["kind"] != "refused"]
    return result.status, result.outputs, result.state, kept


ADVERSARIES = {"refuser": refuser, "idle": "idle", "spammer": spammer, "boundary": boundary, "prober": prober}
