"""Deterministic stand-in hosts for tests and offline runs.

Each stub answers from its inputs alone (a hash, a template or your function), so two runs with
the same contract and seed are identical. Every stub keeps the requests it received in
``calls``, so a test can assert that a replay never reached the host.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

__all__ = ["StubEvaluator", "StubGameMaster", "StubTools", "StubWriter", "StubRanker"]


def _digest(*parts: Any) -> int:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


class _Recording:
    def __init__(self) -> None:
        self.calls: List[Any] = []


class StubEvaluator(_Recording):
    """Scores each criterion from a hash of the text (or with ``scores(request)``)."""

    def __init__(self, scores: Optional[Callable[[Mapping[str, Any]], Mapping[str, float]]] = None,
                 rationale: str = "Scored by the stub evaluator."):
        super().__init__()
        self._scores = scores
        self._rationale = rationale

    def judge(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        self.calls.append(request)
        if self._scores is not None:
            return {"scores": dict(self._scores(request)), "rationale": self._rationale}
        scores: Dict[str, float] = {}
        for criterion in request["criteria"]:
            low, high = criterion["min"], criterion["max"]
            h = _digest(request.get("judge"), criterion["name"], request.get("text"))
            if float(low).is_integer() and float(high).is_integer():
                scores[criterion["name"]] = int(low) + h % (int(high) - int(low) + 1)
            else:
                scores[criterion["name"]] = round(low + (h % 1001) / 1000 * (high - low), 3)
        return {"scores": scores, "rationale": self._rationale}


class StubGameMaster(_Recording):
    """Resolves attempts with ``resolve(request)``; by default nothing happens."""

    def __init__(self, resolve: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None):
        super().__init__()
        self._resolve = resolve

    def resolve(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(request)
        if self._resolve is not None:
            return self._resolve(request)
        return {"narration": "Nothing much happens.", "effects": []}


class StubTools(_Recording):
    """Answers tool calls from a table of results, a function, or a fixed deterministic text."""

    def __init__(self, results: Union[None, Mapping[str, str], Callable[[str, Mapping[str, Any]], str]] = None):
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

    def __init__(self, write: Optional[Callable[[Mapping[str, Any]], str]] = None):
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
            return f"Looking back on {len(memories)} memories, the latest matters most: {memories[-1] if memories else '—'}"
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
