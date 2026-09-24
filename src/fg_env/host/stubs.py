"""Deterministic stand-in hosts for tests and offline runs.

Each stub answers from its inputs alone (a hash, a template or your function), so two runs with
the same contract and seed are identical. Every stub keeps the requests it received in
``calls``, so a test can assert that a replay never reached the host.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

FeedValues = None | Callable[[Mapping[str, Any]], Any] | Mapping[int, Any] | Sequence[Any]

__all__ = ["StubEvaluator", "StubGameMaster", "StubTools", "StubWriter", "StubRanker", "StubFeed", "StubDescriber"]


def _digest(*parts: Any) -> int:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


class _Recording:
    def __init__(self) -> None:
        self.calls: list[Any] = []


class StubEvaluator(_Recording):
    """Scores each criterion from a hash of the text (or with ``scores(request)``)."""

    def __init__(self, scores: Callable[[Mapping[str, Any]], Mapping[str, float]] | None = None,
                 rationale: str = "Scored by the stub evaluator."):
        super().__init__()
        self._scores = scores
        self._rationale = rationale

    def judge(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(request)
        if self._scores is not None:
            return {"scores": dict(self._scores(request)), "rationale": self._rationale}
        scores: dict[str, float] = {}
        for criterion in request["criteria"]:
            low, high = criterion["min"], criterion["max"]
            h = _digest(request.get("judge"), criterion["name"], request.get("text"))
            if float(low).is_integer() and float(high).is_integer():
                scores[criterion["name"]] = int(low) + h % (int(high) - int(low) + 1)
            else:
                scores[criterion["name"]] = round(low + (h % 1001) / 1000 * (high - low), 3)
        return {"scores": scores, "rationale": self._rationale}


class StubGameMaster(_Recording):
    """Resolves attempts with ``resolve(request)``; by default it applies every allowed effect once, within its
    bounds, so what the effects set off is played: on the actor when the rule allows it (else its first target), a
    number moved by the rule's greatest change (up, or down at the top of its range), a flag flipped, the first listed
    value unlike the current one, one unit transferred (the rule's amount when that is less), a move to the first
    destination, news of the attempt. A rule it cannot fill with a value it knows to fit — no target, or a change from
    a number the request does not show — it leaves out."""

    def __init__(self, resolve: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None):
        super().__init__()
        self._resolve = resolve

    def resolve(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(request)
        if self._resolve is not None:
            return self._resolve(request)
        effects: list[dict[str, Any]] = []
        changed = set()
        for rule in request.get("allowed") or []:
            effect = _effect(rule, request)
            key = (effect["effect"], effect.get("target"), effect.get("prop")) if effect else None
            if effect and (effect["effect"] == "transfer" or key not in changed):  # one change to each thing
                changed.add(key)
                effects.append(effect)
        return {"narration": "The stub game master lets it happen.", "effects": effects[:request.get("max_effects")]}


def _effect(rule: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any] | None:
    """The effect ``rule`` allows, filled with values that fit it, or None."""
    actor = request.get("actor") or {}
    kind = rule.get("effect")
    if kind == "news":
        return {"effect": "news", "text": f"News: {request.get('attempt', '')}"[:rule.get("max_chars", 200)]}
    if kind == "transfer":
        giver = _pick(rule.get("from") or [], actor.get("id"))
        receiver = next((party for party in rule.get("to") or [] if party != giver), None)
        return (None if giver is None or receiver is None else
                {"effect": "transfer", "prop": rule["prop"], "from": giver, "to": receiver,
                 "amount": min(1, rule["max_amount"])})
    if kind == "set_world":
        value = _value(rule, None)
        return None if value is None else {"effect": "set_world", "prop": rule["prop"], "value": value}
    target = _pick(rule.get("targets") or [], actor.get("id"))
    if target is None:
        return None
    if kind == "move":
        places = (rule.get("destinations") or {}).get(target) or []
        return {"effect": "move", "target": target, "to": places[0]} if places else None
    value = _value(rule, (actor.get("props") or {}).get(rule["prop"]) if target == actor.get("id") else None)
    return None if value is None else {"effect": "set", "target": target, "prop": rule["prop"], "value": value}


def _pick(ids: Sequence[str], actor: Any) -> str | None:
    return actor if actor in ids else (ids[0] if ids else None)


def _value(rule: Mapping[str, Any], current: Any) -> Any:
    """A new value for ``rule``'s property (``current`` when the request shows it) that fits the rule, or None."""
    if rule.get("values"):
        return next((value for value in rule["values"] if value != current), rule["values"][0])
    if isinstance(current, bool):
        return not current
    low, high, change = rule.get("min"), rule.get("max"), rule.get("max_change")
    if isinstance(current, (int, float)):
        step = change if change is not None else 1
        within = [min(max(value, low if low is not None else value), high if high is not None else value)
                  for value in (current + step, current - step)]
        return next((value for value in within if value != current), None)
    if change is not None or (low is None and high is None):
        return None
    return low if low is not None else high


class StubTools(_Recording):
    """Answers tool calls from a table of results, a function, or a fixed deterministic text."""

    def __init__(self, results: None | Mapping[str, str] | Callable[[str, Mapping[str, Any]], str] = None):
        super().__init__()
        self._results = results

    def call(self, name: str, args: Mapping[str, Any]) -> str:
        self.calls.append({"name": name, "args": dict(args)})
        if callable(self._results):
            return self._results(name, args)
        query = str(args.get("query", json.dumps(dict(args), sort_keys=True)))
        if isinstance(self._results, Mapping) and query in self._results:
            return self._results[query]
        return f"[1] Stub source for '{query}' — offline result {_digest(name, query) % 1000}."


class StubWriter(_Recording):
    """Writes text with ``write(request)``, or from the request's prompt deterministically."""

    def __init__(self, write: Callable[[Mapping[str, Any]], str] | None = None):
        super().__init__()
        self._write = write

    def write(self, request: Mapping[str, Any]) -> str:
        self.calls.append(request)
        if self._write is not None:
            return self._write(request)
        task = request.get("task", "text")
        if task == "persona":
            return f"{request.get('prompt', '').strip()} (persona {_digest(request.get('prompt')) % 100})"
        if task == "recap":
            entries = request.get("entries") or []
            return f"So far: {len(entries)} new entries; the latest reads: {entries[-1]['text'] if entries else '—'}"
        if task == "reflection":
            memories = request.get("memories") or []
            return (f"Looking back on {len(memories)} memories, the latest matters most: "
                    f"{memories[-1] if memories else '—'}")
        return f"{task}: {request.get('prompt', '')}"


class StubRanker(_Recording):
    """Relevance as the share of query words found in each item."""

    def rank(self, request: Mapping[str, Any]) -> Sequence[float]:
        self.calls.append(request)
        words = set(re.findall(r"[a-z0-9]+", str(request.get("query", "")).lower()))
        out = []
        for item in request.get("items") or []:
            found = set(re.findall(r"[a-z0-9]+", str(item.get("text", "")).lower()))
            out.append(len(words & found) / len(words) if words else 0.0)
        return out


class StubFeed(_Recording):
    """Answers feed requests from ``values``: a function of the request, a table by round, or a list
    (round 1 reads the first item, later rounds past its end read the last); with nothing, a
    deterministic number from 0 to 100 derived from the feed, query and round."""

    def __init__(self, values: FeedValues = None):
        super().__init__()
        self._values = values

    def fetch(self, request: Mapping[str, Any]) -> Any:
        self.calls.append(request)
        values, moment = self._values, request.get("round")
        if callable(values):
            return values(request)
        if isinstance(values, Mapping):
            return values.get(moment)
        if isinstance(values, Sequence) and not isinstance(values, str) and values:
            return values[min(max(0, int(moment or 1) - 1), len(values) - 1)]
        return (_digest(request.get("feed"), request.get("query"), moment) % 10_001) / 100


class StubDescriber(_Recording):
    """Describes files with ``describe(request)``, or from their metadata: the caption names the file's type and name,
    and a text file's content is its text."""

    def __init__(self, describe: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None):
        super().__init__()
        self._describe = describe

    def describe(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(request)
        if self._describe is not None:
            return self._describe(request)
        asset = request.get("asset") or {}
        files = request.get("attachments") or []
        text = next((item["text"] for item in files if isinstance(item.get("text"), str)), "")
        return {"caption": f"A {asset.get('type', 'file')} named {asset.get('name', '')}", "text": text}
