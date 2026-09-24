"""Reading a recorded run: what each agent read, which tools it had, what it called and what came back."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union

from ..runtime.measure import RunResult
from ..runtime.result_file import load_result, result_from_dict
from . import render

if TYPE_CHECKING:
    from .rerun import ReplayResult

__all__ = ["Trace", "TraceView", "trace"]

TraceSource = Union["Trace", RunResult, Mapping[str, Any], str, "os.PathLike[str]"]
_USAGE = ("llm_calls", "input_tokens", "output_tokens")


@dataclass(frozen=True)
class TraceView:
    """One reading of a trace: ``data`` (plain JSON data for code) and ``text`` (for people, also ``str()``)."""

    data: Any
    text: str

    def __str__(self) -> str:
        return self.text


def trace(source: TraceSource) -> Trace:
    """A recorded run to read: a :class:`RunResult`, its ``to_dict()``, or a file written by ``result.save()``.

    The run must have recorded exposures: ``fg_env.run(..., exposures=True)``.
    """
    return Trace(source)


class Trace:
    """A recorded run. ``wakes`` are the exposure records in engine order; ``texts`` holds every text by hash."""

    def __init__(self, source: TraceSource):
        result: RunResult
        if isinstance(source, Trace):
            result = source.result
        elif isinstance(source, RunResult):
            result = source
        elif isinstance(source, Mapping):
            result = result_from_dict(source)
        elif isinstance(source, (str, os.PathLike)):
            result = load_result(source)
        else:
            raise TypeError("a trace is read from a RunResult, its dict, or a saved result file; got "
                            f"{type(source).__name__}")
        if not result.exposures:
            raise ValueError("this run did not record what its agents saw; run it with exposures=True "
                             "(fg_env.run(..., exposures=True), or fg-env run --trace FILE)")
        self.result: RunResult = result
        self.texts: dict[str, str] = dict(result.exposures.get("texts") or {})
        self.wakes: list[dict[str, Any]] = list(result.exposures.get("wakes") or [])

    @property
    def entities(self) -> list[str]:
        """Every agent that was woken, in the order it first was."""
        return list(dict.fromkeys(wake["entity"] for wake in self.wakes))

    def text(self, digest: str | None) -> str | None:
        return None if digest is None else self.texts.get(digest, "")

    # -- readings ----------------------------------------------------------------------------

    def overview(self) -> TraceView:
        """The run, and per agent: turns, calls, invalid and rejected calls, timeouts, undone turns, model tokens."""
        rows: dict[str, dict[str, Any]] = {}
        for wake in self.wakes:
            row = rows.get(wake["entity"])
            if row is None:
                row = rows[wake["entity"]] = _row(wake["entity"], wake["type"])
            _count(row, wake)
        agents = [_rated(row) for row in rows.values()]
        totals = _rated(_row("all", ""))
        for row in agents:
            for key in totals:
                if isinstance(totals[key], int) and key != "invalid_rate":
                    totals[key] += row[key]
        totals = _rated(totals)
        run = self.result
        data = {"run": {"status": run.status, "ended_by": run.ended_by, "rounds": run.rounds, "seed": run.seed,
                        "arm": run.arm, "winner": run.winner, "wakes": len(self.wakes), "budget": run.budget or None},
                "agents": agents, "totals": totals}
        return TraceView(data, render.overview(data))

    def turn(self, which: int | str, round: int | None = None) -> TraceView:
        """Wakes in full — what the agent read, the tools offered, every call with its result. ``turn(7)`` is wake 7;
        ``turn("ana", 3)`` every wake of ``ana`` in round 3. ``data`` is a list of wakes."""
        data = [self._full(wake) for wake in self._select(which, round)]
        return TraceView(data, render.turns(data))

    def timeline(self, entity: str | None = None) -> TraceView:
        """One line per wake (of ``entity``, or everyone): when, who, and each call with its outcome."""
        wakes = self._of(entity) if entity is not None else self.wakes
        rows = [_moment(wake) for wake in wakes]
        return TraceView(rows, render.timeline(rows))

    def search(self, text: str) -> TraceView:
        """Every place ``text`` (any case) appears in what agents read — briefs, updates, tool definitions, call
        results — or wrote as call arguments. A brief or tool set is reported the first time each agent read it."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("search needs some text to look for")
        pattern = re.compile(re.escape(text), re.IGNORECASE)
        matching = {digest for digest, body in self.texts.items() if pattern.search(body)}
        hits: list[dict[str, Any]] = []
        reported: set = set()

        def hit(wake: Mapping[str, Any], where: str, body: str) -> None:
            hits.append({**_where(wake), "where": where, "snippet": _snippet(body, pattern)})

        for wake in self.wakes:
            for key in ("brief", "update"):
                shown = wake[key]
                if shown is not None and shown["hash"] in matching and (
                        key != "brief" or (wake["entity"], shown["hash"]) not in reported):
                    reported.add((wake["entity"], shown["hash"]))
                    hit(wake, key, self.texts[shown["hash"]])
            for digest in wake["tool_sets"]:
                if digest in matching and (wake["entity"], digest) not in reported:
                    reported.add((wake["entity"], digest))
                    hit(wake, "tools", self.texts[digest])
            for index, call in enumerate(wake["calls"], 1):
                written = json.dumps(call["args"], ensure_ascii=False)
                if pattern.search(written):
                    hit(wake, f"call {index} {call['tool']} arguments", written)
                if call["result"] in matching:
                    hit(wake, f"call {index} {call['tool']} result", self.texts[call["result"]])
        return TraceView(hits, render.search(hits, text))

    def invalid(self, entity: str | None = None) -> TraceView:
        """Every call that did not go through — invalid, rejected, undone, out of time — with the correction the
        agent was given."""
        rows = [{**_where(wake), "call": index, "tool": call["tool"], "args": call["args"],
                 "error": call.get("error", "refused"), "correction": self.texts.get(call["result"], "")}
                for wake in (self._of(entity) if entity is not None else self.wakes)
                for index, call in enumerate(wake["calls"], 1) if not call["ok"]]
        return TraceView(rows, render.invalid(rows))

    def agent(self, entity: str) -> TraceView:
        """One agent: its totals, how each tool it called went, and its timeline."""
        wakes = self._of(entity)
        row = _row(entity, wakes[0]["type"])
        tools: dict[str, dict[str, int]] = {}
        for wake in wakes:
            _count(row, wake)
            for call in wake["calls"]:
                tally = tools.setdefault(call["tool"], {"calls": 0, "ok": 0, "refused": 0})
                tally["calls"] += 1
                tally["ok" if call["ok"] else "refused"] += 1
        data = {**_rated(row), "tools": tools, "timeline": [_moment(wake) for wake in wakes]}
        return TraceView(data, render.agent(data))

    def replay(self, contract: Any, *, fallback: Any = None, hosts: Mapping[str, Any] | None = None,
               data_dir: Any = None) -> ReplayResult:
        """Run ``contract`` again from the recording (its seed, inputs, arm, steps and host answers) and report the
        first divergence; see :func:`fg_env.trace.rerun.replay_run`."""
        from .rerun import replay_run

        return replay_run(self, contract, fallback=fallback, hosts=hosts, data_dir=data_dir)

    # -- helpers -----------------------------------------------------------------------------

    def _of(self, entity: str) -> list[dict[str, Any]]:
        wakes = [wake for wake in self.wakes if wake["entity"] == entity]
        if not wakes:
            raise ValueError(f"'{entity}' was never woken in this run (agents: "
                             f"{', '.join(self.entities[:20]) or 'none'})")
        return wakes

    def _select(self, which: int | str, round: int | None) -> list[dict[str, Any]]:
        if isinstance(which, int) and not isinstance(which, bool):
            if round is not None:
                raise ValueError("give a wake number, or an agent and a round — not a wake number and a round")
            if not 0 <= which < len(self.wakes):
                raise ValueError(f"no wake {which}: this run has wakes 0 to {len(self.wakes) - 1}" if self.wakes
                                 else "this run woke no agent")
            return [self.wakes[which]]
        if not isinstance(which, str):
            raise ValueError(f"a turn is a wake number or an agent id, got {which!r}")
        wakes = self._of(which)
        if round is None:
            raise ValueError(f"give the round too, like turn('{which}', {wakes[0]['round']})")
        chosen = [wake for wake in wakes if wake["round"] == round]
        if not chosen:
            rounds = ", ".join(str(r) for r in dict.fromkeys(wake["round"] for wake in wakes))
            raise ValueError(f"'{which}' was not woken in round {round} (rounds it was woken: {rounds})")
        return chosen

    def _full(self, wake: Mapping[str, Any]) -> dict[str, Any]:
        out = {key: wake[key] for key in ("wake", "entity", "type", "round", "stage", "turn", "kind", "reason")}
        for key in ("time", "time_limit"):
            if key in wake:
                out[key] = wake[key]
        out.update(
            brief=self.text(wake["brief"]["hash"]) if wake["brief"] else None,
            update=self.text(wake["update"]["hash"]) if wake["update"] else None,
            views=[{"name": view["name"], "look": bool(view.get("look")), "text": self.texts.get(view["hash"], "")}
                   for view in wake["views"]],
            news=list(wake["news"]), tools=list(wake["tools"]),
            tool_sets=[json.loads(self.texts[digest]) for digest in wake["tool_sets"] if digest in self.texts],
            calls=[{"tool": call["tool"], "args": call["args"], "ok": call["ok"], "ended": call["ended"],
                    **({"error": call["error"]} if "error" in call else {}),
                    "result": self.texts.get(call["result"], "")} for call in wake["calls"]],
            invalid=wake["invalid"], timed_out=wake["timed_out"], undone=wake["undone"],
            usage=dict(wake.get("usage") or {}))
        return out


def _row(entity: str, kind: str) -> dict[str, Any]:
    return {"entity": entity, "type": kind, "turns": 0, "calls": 0, "invalid": 0, "rejected": 0, "invalid_rate": 0.0,
            "timeouts": 0, "undone": 0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0}


def _count(row: dict[str, Any], wake: Mapping[str, Any]) -> None:
    row["turns"] += 1
    row["calls"] += len(wake["calls"])
    row["invalid"] += wake["invalid"]
    row["rejected"] += sum(1 for call in wake["calls"] if call.get("error") == "rejected")
    row["timeouts"] += int(bool(wake["timed_out"]))
    row["undone"] += wake["undone"]
    usage = wake.get("usage") or {}
    for key in _USAGE:
        row[key] += usage.get(key, 0)


def _rated(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "invalid_rate": round(row["invalid"] / row["calls"], 3) if row["calls"] else 0.0}


def _where(wake: Mapping[str, Any]) -> dict[str, Any]:
    return {"wake": wake["wake"], "entity": wake["entity"], "round": wake["round"], "stage": wake["stage"]}


def _moment(wake: Mapping[str, Any]) -> dict[str, Any]:
    row = {**_where(wake), "kind": wake["kind"]}
    if "time" in wake:
        row["time"] = wake["time"]
    row["calls"] = [{"tool": call["tool"], "args": call["args"], "ok": call["ok"],
                     **({"error": call["error"]} if "error" in call else {})} for call in wake["calls"]]
    row.update(timed_out=wake["timed_out"], undone=wake["undone"])
    return row


#: Characters of context shown on each side of a search match.
_CONTEXT = 40


def _snippet(body: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(body)
    if match is None:
        return ""
    start, end = max(0, match.start() - _CONTEXT), min(len(body), match.end() + _CONTEXT)
    text = " ".join(body[start:end].split())
    return ("…" if start else "") + text + ("…" if end < len(body) else "")
